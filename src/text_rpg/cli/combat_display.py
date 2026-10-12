"""Combat-specific display helpers — turn-based combat UI."""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


class CombatDisplay:
    def __init__(self) -> None:
        self.console = console

    def show_combat_start(self, enemies: list[dict], fight_type: str = "attrition") -> None:
        enemy_names = ", ".join(e.get("name", "Unknown") for e in enemies)
        type_label = {"boss": "[bold magenta]BOSS FIGHT[/bold magenta]", "attrition": "[bold red]COMBAT![/bold red]"}.get(fight_type, "[bold red]COMBAT![/bold red]")
        self.console.print(Panel(
            f"{type_label}\n\nHostile creatures engage: {enemy_names}",
            border_style="magenta" if fight_type == "boss" else "red", box=box.HEAVY,
        ))

    def show_initiative_order(self, combatants: list[dict]) -> None:
        """Show initiative order as a horizontal bar."""
        parts = []
        for c in combatants:
            color = "green" if c.get("combatant_type") == "player" else "red"
            init = c.get("initiative", 0)
            parts.append(f"[{color}]{c.get('name', '?')}({init})[/{color}]")
        self.console.print(f"\n[bold]Initiative:[/bold] {' > '.join(parts)}")

    def show_combat_menu(self, combat_state: dict, character: dict, enemies: list[dict]) -> None:
        """Show Pokemon-style combat action menu with enemy HP bars."""
        round_num = combat_state.get("round_number", 1)

        content = Text()
        pending = any(c.get("opening_pending") for c in combat_state.get("combatants", []))
        content.append(f"Round {round_num} — {'Choose your opening action' if pending else 'Your turn'}\n", style="bold yellow")
        if pending:
            content.append("Initiative determines who acts first.\n", style="dim")
        combatants = {c.get("entity_id"): c for c in combat_state.get("combatants", [])}
        ordered = [combatants[entity_id].get("name", "?")
                   for entity_id in combat_state.get("turn_order", []) if entity_id in combatants]
        if ordered:
            content.append("Initiative: " + " → ".join(ordered) + "\n", style="cyan")
        for unit, player in [(e, False) for e in enemies] + [(character, True)]:
            hp = unit.get("hp")
            current = hp.get("current", 0) if isinstance(hp, dict) else unit.get("hp_current", 0)
            maximum = hp.get("max", 10) if isinstance(hp, dict) else unit.get("hp_max", 10)
            pct = max(0, min(1, current / max(maximum, 1)))
            filled = int(pct * 12)
            color = "green" if pct > .5 else "yellow" if pct > .25 else "red"
            content.append("\n" + ("You" if player else unit.get("name", "Enemy")) + "  ", style="bold")
            content.append("█" * filled, style=color)
            content.append("░" * (12 - filled), style="dim")
            content.append(f" {current}/{maximum}\n")
            if unit.get("conditions"):
                content.append(", ".join(str(c) for c in unit["conditions"]) + "\n", style="yellow")
            if not player:
                content.append("Intent: " + self._enemy_intent(unit, character) + "\n", style="magenta")
        content.append("\n")
        living = [enemy for enemy in enemies if enemy.get("is_active", True)]
        attack_label = (f"Attack {living[0].get('name', 'enemy')}" if len(living) == 1
                        else "Attack <target>")
        labels = [attack_label, "Cast Spell", "Use Item", "Flee", "Dodge"]
        ability = self._get_class_ability_label(character)
        if ability:
            labels.append(ability)
        for number, label in enumerate(labels, 1):
            content.append(f"[{number}] ", style="bold cyan")
            content.append(label + "\n")
        content.append("\nOther: grapple <target>, disengage, dash\n", style="dim")
        self.console.print(Panel(content, border_style="red", box=box.ROUNDED, width=min(76, self.console.width)))

    @staticmethod
    def _enemy_intent(enemy: dict, character: dict) -> str:
        """Telegraph the deterministic AI choice represented by current state."""
        hp = enemy.get("hp", {})
        current = hp.get("current", 0) if isinstance(hp, dict) else enemy.get("hp_current", 0)
        maximum = hp.get("max", 1) if isinstance(hp, dict) else enemy.get("hp_max", 1)
        if maximum and current / maximum < 0.25:
            return "trying to escape"
        attacks = enemy.get("attacks") or []
        index = int(enemy.get("next_attack_index", 0)) % len(attacks) if attacks else 0
        attack = attacks[index] if attacks and isinstance(attacks[index], dict) else {}
        attack_name = attack.get("name") or attack.get("attack_name") or "a direct attack"
        return f"{attack_name} against you (AC {character.get('ac', 10)})"

    def show_threat_warning(self, threats: list[tuple[str, str]]) -> None:
        """Show danger warnings before combat. threats is [(name, level)]."""
        if not threats:
            return
        lines = []
        for name, threat in threats:
            if threat == "overwhelming":
                lines.append(f"[bold red]OVERWHELMING:[/bold red] {name} — [red]This foe will destroy you![/red]")
            elif threat == "deadly":
                lines.append(f"[bold red]DEADLY:[/bold red] {name} — [red]You sense great danger.[/red]")
        if lines:
            self.console.print(Panel(
                "\n".join(lines),
                title="[bold red]Threat Assessment[/bold red]",
                border_style="red", box=box.HEAVY,
            ))

    @staticmethod
    def _get_class_ability_label(character: dict) -> str | None:
        """Return the label for a class-specific combat ability, or None."""
        char_class = (character.get("char_class") or "").lower()
        _CLASS_ABILITIES = {
            "barbarian": "Rage",
            "bard": "Inspire",
            "monk": "Flurry",
            "paladin": "Lay on Hands",
            "druid": "Wild Shape",
        }
        return _CLASS_ABILITIES.get(char_class)

    def show_turn_start(self, combatant_name: str, is_player: bool) -> None:
        if is_player:
            self.console.print("\n[bold green]--- Your Turn ---[/bold green]")
        else:
            self.console.print(f"\n[bold red]--- {combatant_name}'s Turn ---[/bold red]")

    def show_attack_result(
        self, attacker: str, defender: str, hit: bool, is_critical: bool,
        roll_total: int, target_ac: int, damage: int | None = None, damage_type: str = "",
    ) -> None:
        if is_critical:
            self.console.print(f"  [bold yellow]CRITICAL HIT![/bold yellow] {attacker} rolls {roll_total} vs AC {target_ac}")
            if damage is not None:
                self.console.print(f"  [red]{attacker} deals {damage} {damage_type} damage to {defender}![/red]")
        elif hit:
            self.console.print(f"  [green]Hit![/green] {attacker} rolls {roll_total} vs AC {target_ac}")
            if damage is not None:
                self.console.print(f"  [red]{attacker} deals {damage} {damage_type} damage to {defender}.[/red]")
        else:
            self.console.print(f"  [dim]Miss.[/dim] {attacker} rolls {roll_total} vs AC {target_ac}")

    def show_defeat(self, entity_name: str) -> None:
        self.console.print(f"\n  [bold red]{entity_name} has been defeated![/bold red]")

    def show_combat_end(
        self, victory: bool, xp_gained: int = 0,
        loot: list[str] | None = None, gold_looted: int = 0,
        gold_lost: int = 0, respawn_location: str | None = None,
    ) -> None:
        if victory:
            content = "[bold green]Victory![/bold green]\n"
            if xp_gained:
                content += f"\nXP Gained: {xp_gained}"
            if gold_looted:
                content += f"\nGold: +{gold_looted} gp"
            if loot:
                content += f"\nLoot: {', '.join(loot)}"
            self.console.print(Panel(content, border_style="green", box=box.HEAVY))
        else:
            content = "[bold red]Defeat...[/bold red]\n\nDarkness claims you..."
            if gold_lost:
                content += f"\n\n[dim]You lost {gold_lost} gold.[/dim]"
            if respawn_location:
                content += f"\n[dim]You awaken at {respawn_location}, weakened.[/dim]"
            self.console.print(Panel(content, border_style="red", box=box.HEAVY))

    def show_combat_round(self, turn_descriptions: list[str]) -> None:
        """Show what happened this round."""
        if not turn_descriptions:
            return
        self.console.print()
        for desc in turn_descriptions:
            self.console.print(f"  {desc}")

    def show_combat_result(self, result: str, xp: int) -> None:
        """Show victory/defeat/fled summary."""
        if result == "victory":
            self.show_combat_end(True, xp)
        elif result == "defeat":
            self.show_combat_end(False)
        elif result == "fled":
            self.console.print(Panel(
                "[bold yellow]Escaped![/bold yellow]\nYou flee from combat.",
                border_style="yellow", box=box.HEAVY,
            ))
