"""Persistent status bar — rendered before each prompt."""
from __future__ import annotations

from rich.console import Console
from rich.text import Text

from text_rpg.utils import safe_json


class StatusBar:
    """Compact one-line status header shown before each game prompt."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, character: dict, location_name: str, world_time: dict | None = None) -> None:
        """Print a compact status line: HP | Mana | Gold | Time | Location."""
        line = Text()

        # Character name
        name = character.get("name", "")
        if name:
            line.append(name, style="bold cyan")
            line.append(" | ", style="dim")

        # HP bar
        hp_cur = character.get("hp_current", 0)
        hp_max = character.get("hp_max", 1)
        hp_pct = hp_cur / max(hp_max, 1)
        hp_color = "green" if hp_pct > 0.5 else ("yellow" if hp_pct > 0.25 else "red")
        bar_w = 8
        filled = int(hp_pct * bar_w)
        hp_bar = f"{'█' * filled}{'░' * (bar_w - filled)}"
        line.append("HP ", style="bold")
        line.append(f"[{hp_bar}]", style=hp_color)
        line.append(f" {hp_cur}/{hp_max}", style=hp_color)

        # Spell slots (compact)
        if character.get("spellcasting_ability"):
            slots_remaining = safe_json(character.get("spell_slots_remaining"), {})
            slots_max = safe_json(character.get("spell_slots_max"), {})
            if slots_max:
                line.append(" | ", style="dim")
                slot_parts = []
                for lvl_key in sorted(slots_max, key=lambda k: int(k)):
                    remaining = int(slots_remaining.get(str(lvl_key), slots_remaining.get(int(lvl_key), 0)))
                    maximum = int(slots_max.get(str(lvl_key), slots_max.get(int(lvl_key), 0)))
                    filled_s = "O" * remaining + "." * (maximum - remaining)
                    slot_parts.append(f"Lv{lvl_key}:[{filled_s}]")
                line.append(" ".join(slot_parts), style="cyan")

        # Gold
        gold = character.get("gold", 0)
        line.append(" | ", style="dim")
        line.append(f"{gold} gp", style="yellow")

        # Time of day
        if world_time:
            time_of_day = world_time.get("time_of_day", "")
            day = world_time.get("day", 0)
            if time_of_day:
                line.append(" | ", style="dim")
                time_icons = {
                    "dawn": "Dawn",
                    "morning": "Morning",
                    "midday": "Midday",
                    "afternoon": "Afternoon",
                    "dusk": "Dusk",
                    "evening": "Evening",
                    "night": "Night",
                    "midnight": "Midnight",
                }
                label = time_icons.get(time_of_day, time_of_day.title())
                line.append(label, style="dim")
                if day:
                    line.append(f" D{day}", style="dim")
                if world_time.get("clock"):
                    line.append(f" {world_time['clock']}", style="dim")

        # Location
        if location_name:
            line.append(" | ", style="dim")
            line.append(location_name, style="bold white")

        self.console.print(line)
