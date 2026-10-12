"""Interactive character creation flow using Rich."""
from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

ABILITY_DESCRIPTIONS: dict[str, str] = {
    "strength": "Melee attacks, carrying capacity, athletic feats. Key for fighters and melee builds.",
    "dexterity": "Ranged attacks, AC, initiative, stealth, acrobatics. Key for rogues and ranged builds.",
    "constitution": "Hit points, concentration saves, endurance. Important for every class.",
    "intelligence": "Arcana, history, investigation. Key for wizards. Affects knowledge checks.",
    "wisdom": "Perception, insight, survival, medicine. Key for clerics. Resists charms and illusions.",
    "charisma": "Persuasion, deception, intimidation, performance. Key for social encounters.",
}

SKILL_DESCRIPTIONS: dict[str, str] = {
    "acrobatics": "Balance, tumbling, and aerial maneuvers (DEX)",
    "animal_handling": "Calm, control, or intuit an animal's intentions (WIS)",
    "arcana": "Recall lore about spells, magic items, and planes (INT)",
    "athletics": "Climb, jump, swim, and feats of raw strength (STR)",
    "deception": "Mislead others through words or actions (CHA)",
    "history": "Recall lore about events, people, and civilizations (INT)",
    "insight": "Read body language and detect lies or hidden motives (WIS)",
    "intimidation": "Influence through threats and hostile presence (CHA)",
    "investigation": "Deduce information from clues and evidence (INT)",
    "medicine": "Stabilize the dying and diagnose illness (WIS)",
    "nature": "Recall lore about terrain, plants, and animals (INT)",
    "perception": "Spot, hear, or detect hidden things. Passive awareness (WIS)",
    "performance": "Entertain through music, dance, or storytelling (CHA)",
    "persuasion": "Influence others through tact, diplomacy, and charm (CHA)",
    "religion": "Recall lore about deities, rites, and holy symbols (INT)",
    "sleight_of_hand": "Pick pockets, conceal objects, and manual trickery (DEX)",
    "stealth": "Move unseen and unheard (DEX)",
    "survival": "Track creatures, navigate, and forage for food (WIS)",
}

# Sentinel for "go back"
_BACK = "__BACK__"


class CharacterCreator:
    def __init__(self) -> None:
        self.console = console

    def run(
        self,
        races: dict[str, dict],
        classes: dict[str, dict],
        origins: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.console.print(Panel(
            "[bold]Character Creation[/bold]\n\nForge your hero for the adventures ahead.\n"
            "[dim]Type 'back' at any step to return to the previous one.[/dim]",
            border_style="cyan", box=box.DOUBLE,
        ))

        # Stage-based flow with back support
        stages = ["name", "race", "class", "origin", "origin_abilities", "abilities", "skills", "confirm"]
        state: dict[str, Any] = {}
        idx = 0

        while idx < len(stages):
            stage = stages[idx]

            if stage == "name":
                result = self._get_name(state.get("name"))
                if result == _BACK:
                    self.console.print("[dim]Already at the first step.[/dim]")
                    continue
                state["name"] = result

            elif stage == "race":
                result = self._choose_race(races, state.get("race"))
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["race"] = result

            elif stage == "class":
                result = self._choose_class(classes, state.get("class"))
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["class"] = result

            elif stage == "origin":
                if not origins:
                    idx += 1
                    continue
                result = self._choose_origin(
                    origins, state["race"], state["class"],
                    state.get("origin"),
                )
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["origin"] = result

            elif stage == "origin_abilities":
                origin = state.get("origin")
                if not origin:
                    idx += 1
                    continue
                result = self._assign_origin_bonuses(
                    origin, state.get("origin_primary"), state.get("origin_secondary"),
                )
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["origin_primary"] = result[0]
                state["origin_secondary"] = result[1]

            elif stage == "abilities":
                result = self._assign_ability_scores(state.get("abilities"))
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["abilities"] = result

            elif stage == "skills":
                class_data = classes[state["class"]]
                origin = state.get("origin")
                origin_skills = origin.get("skill_proficiencies", []) if origin else []
                result = self._choose_skills(
                    class_data, state.get("skills"), origin_skills=origin_skills,
                )
                if result == _BACK:
                    idx = max(0, idx - 1)
                    continue
                state["skills"] = result

            elif stage == "confirm":
                action = self._show_summary_and_confirm(
                    state["name"], races[state["race"]], classes[state["class"]],
                    state["abilities"], state["skills"],
                    origin=state.get("origin"),
                    origin_primary=state.get("origin_primary"),
                    origin_secondary=state.get("origin_secondary"),
                )
                if action == _BACK:
                    idx = max(0, idx - 1)
                    continue
                if action == "edit":
                    edit_idx = self._pick_stage_to_edit(has_origin=bool(state.get("origin")))
                    if edit_idx is not None:
                        idx = edit_idx
                    continue
                # action == "confirm"
                break

            idx += 1

        result_dict: dict[str, Any] = {
            "name": state["name"],
            "race": state["race"],
            "char_class": state["class"],
            "ability_scores": state["abilities"],
            "skill_choices": state["skills"],
        }
        if state.get("origin"):
            result_dict["origin"] = state["origin"]
            result_dict["origin_primary"] = state.get("origin_primary")
            result_dict["origin_secondary"] = state.get("origin_secondary")
        return result_dict

    # --- Name ---

    def _get_name(self, current: str | None = None) -> str:
        if current:
            self.console.print(f"\n[bold]Character Name[/bold] [dim](current: {current})[/dim]")
        else:
            self.console.print("\n[bold]What is your name, adventurer?[/bold]")
        while True:
            name = self.console.input("[bold cyan]Name > [/bold cyan]").strip()
            if name.lower() == "back":
                return _BACK
            if name and len(name) <= 30:
                return name
            self.console.print("[red]Please enter a name (1-30 characters).[/red]")

    # --- Race ---

    def _show_race_list(self, race_list: list[dict]) -> None:
        for i, race in enumerate(race_list, 1):
            bonuses = race.get("ability_bonuses", {})
            bonus_str = ", ".join(f"{k[:3].upper()} +{v}" for k, v in bonuses.items())
            traits = [t["name"] for t in race.get("traits", [])]
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{race['name']}[/bold] ({bonus_str})")
            self.console.print(f"     {race['description'][:80]}...")
            self.console.print(f"     [dim]Traits: {', '.join(traits)}[/dim]")

    def _inspect_race(self, race: dict) -> None:
        bonuses = race.get("ability_bonuses", {})
        bonus_str = ", ".join(f"{k[:3].upper()} +{v}" for k, v in bonuses.items())
        content = f"[bold]{race['name']}[/bold] ({bonus_str})\n\n{race['description']}\n"
        content += f"\n[bold]Speed:[/bold] {race.get('speed', 30)} ft."
        for trait in race.get("traits", []):
            content += f"\n[bold]{trait['name']}:[/bold] {trait.get('description', '')}"
        self.console.print(Panel(content, border_style="cyan", box=box.ROUNDED))

    def _choose_race(self, races: dict[str, dict], current: str | None = None) -> str:
        self.console.print("\n[bold]Choose your race:[/bold]")
        if current:
            self.console.print(f"[dim]Current: {current} | Type 'back' to go back, ? to inspect (e.g. 1?)[/dim]\n")
        else:
            self.console.print("[dim]Type a number to select, ? to inspect (e.g. 1?), or 'back'[/dim]\n")
        race_list = list(races.values())
        self._show_race_list(race_list)
        while True:
            choice = self.console.input("\n[bold cyan]Race > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK
            if choice.endswith("?"):
                lookup = choice[:-1].strip()
                found = self._find_by_index_or_name(race_list, lookup)
                if found:
                    self._inspect_race(found)
                else:
                    self.console.print("[red]Not found. Try a number or name.[/red]")
                continue
            found = self._find_by_index_or_name(race_list, choice)
            if found:
                return found["id"]
            self.console.print("[red]Invalid choice. Enter a number, name, or ? to inspect.[/red]")

    # --- Class ---

    def _show_class_list(self, class_list: list[dict]) -> None:
        for i, cls in enumerate(class_list, 1):
            features_l1 = [f["name"] for f in cls.get("features", []) if f.get("level") == 1]
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{cls['name']}[/bold] (Hit Die: {cls['hit_die']})")
            self.console.print(f"     {cls['description'][:80]}...")
            if features_l1:
                self.console.print(f"     [dim]Level 1: {', '.join(features_l1)}[/dim]")

    def _inspect_class(self, cls: dict) -> None:
        content = f"[bold]{cls['name']}[/bold] (Hit Die: {cls['hit_die']})\n\n{cls['description']}\n"
        content += f"\n[bold]Primary Ability:[/bold] {cls.get('primary_ability', 'N/A')}"
        content += f"\n[bold]Saving Throws:[/bold] {', '.join(cls.get('saving_throws', []))}"
        content += f"\n[bold]Skill Choices:[/bold] {cls.get('skill_choices', 2)} from {', '.join(s.replace('_', ' ').title() for s in cls.get('available_skills', []))}"
        features = cls.get("features", [])
        if features:
            content += "\n\n[bold]Features:[/bold]"
            for f in sorted(features, key=lambda x: x.get("level", 1)):
                content += f"\n  Lv{f.get('level', '?')}: [bold]{f['name']}[/bold] — {f.get('description', '')}"
        self.console.print(Panel(content, border_style="cyan", box=box.ROUNDED))

    def _choose_class(self, classes: dict[str, dict], current: str | None = None) -> str:
        self.console.print("\n[bold]Choose your class:[/bold]")
        if current:
            self.console.print(f"[dim]Current: {current} | Type 'back' to go back, ? to inspect (e.g. 1?)[/dim]\n")
        else:
            self.console.print("[dim]Type a number to select, ? to inspect (e.g. 1?), or 'back'[/dim]\n")
        class_list = list(classes.values())
        self._show_class_list(class_list)
        while True:
            choice = self.console.input("\n[bold cyan]Class > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK
            if choice.endswith("?"):
                lookup = choice[:-1].strip()
                found = self._find_by_index_or_name(class_list, lookup)
                if found:
                    self._inspect_class(found)
                else:
                    self.console.print("[red]Not found. Try a number or name.[/red]")
                continue
            found = self._find_by_index_or_name(class_list, choice)
            if found:
                return found["id"]
            self.console.print("[red]Invalid choice. Enter a number, name, or ? to inspect.[/red]")

    # --- Ability Scores ---

    def _show_ability_table(self, scores: dict[str, int]) -> None:
        table = Table(box=box.SIMPLE, border_style="cyan", show_header=True)
        table.add_column("#", style="cyan", width=3)
        table.add_column("Ability", style="bold", width=14)
        table.add_column("Score", justify="center", width=6)
        table.add_column("Mod", justify="center", width=5)
        table.add_column("Description", style="dim")
        abilities = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
        for i, ability in enumerate(abilities, 1):
            val = scores.get(ability, "-")
            if isinstance(val, int):
                mod = (val - 10) // 2
                mod_str = f"+{mod}" if mod >= 0 else str(mod)
            else:
                mod_str = "-"
            table.add_row(
                str(i), ability.capitalize(), str(val), mod_str,
                ABILITY_DESCRIPTIONS.get(ability, ""),
            )
        self.console.print(table)

    def _assign_ability_scores(self, current: dict[str, int] | None = None) -> Any:
        self.console.print("\n[bold]Assign Ability Scores[/bold]")
        self.console.print("[dim]Standard Array: 15, 14, 13, 12, 10, 8[/dim]")
        self.console.print("[dim]Type 'back' to go back[/dim]\n")

        abilities = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]

        # Show what each ability does
        info_table = Table(box=box.SIMPLE, border_style="dim", show_header=False, padding=(0, 1))
        info_table.add_column("Ability", style="bold", width=14)
        info_table.add_column("Description", style="dim")
        for ability in abilities:
            info_table.add_row(ability.capitalize(), ABILITY_DESCRIPTIONS[ability])
        self.console.print(info_table)
        self.console.print()

        if current:
            scores = dict(current)
            self.console.print("[dim]Previous assignment loaded. Type 'clear' to start fresh, or 'swap' to swap two.[/dim]")
            self._show_ability_table(scores)
        else:
            scores = {}

        while True:
            # If all assigned, go to review
            if len(scores) == 6:
                self.console.print("\n[bold]Current assignment:[/bold]")
                self._show_ability_table(scores)
                self.console.print("[dim]Commands: 'confirm', 'swap <#> <#>' (e.g. swap 1 3), 'clear', 'back'[/dim]")
                cmd = self.console.input("\n[bold cyan]Scores > [/bold cyan]").strip().lower()
                if cmd == "back":
                    return _BACK
                if cmd == "confirm" or cmd == "done" or cmd == "y":
                    return scores
                if cmd == "clear":
                    scores = {}
                    self.console.print("[yellow]Scores cleared.[/yellow]\n")
                    continue
                if cmd.startswith("swap"):
                    parts = cmd.split()
                    if len(parts) == 3:
                        try:
                            a_idx = int(parts[1]) - 1
                            b_idx = int(parts[2]) - 1
                            if 0 <= a_idx < 6 and 0 <= b_idx < 6 and a_idx != b_idx:
                                a_key = abilities[a_idx]
                                b_key = abilities[b_idx]
                                scores[a_key], scores[b_key] = scores[b_key], scores[a_key]
                                self.console.print(f"[green]Swapped {a_key.capitalize()} and {b_key.capitalize()}.[/green]")
                                continue
                        except ValueError:
                            pass
                    self.console.print("[red]Usage: swap <#> <#> (e.g. swap 1 3)[/red]")
                    continue
                self.console.print("[red]Type 'confirm', 'swap <#> <#>', 'clear', or 'back'.[/red]")
                continue

            # Assignment phase
            available = sorted(set([15, 14, 13, 12, 10, 8]) - set(scores.values()), reverse=True)
            next_ability = None
            for a in abilities:
                if a not in scores:
                    next_ability = a
                    break

            if len(available) == 1:
                scores[next_ability] = available[0]
                self.console.print(f"  [bold cyan]{next_ability.capitalize()}[/bold cyan] = [cyan]{available[0]}[/cyan] (auto-assigned)")
                continue

            self.console.print(f"  Available: [cyan]{', '.join(str(v) for v in available)}[/cyan]")
            val_str = self.console.input(f"  [bold cyan]{next_ability.capitalize()} > [/bold cyan]").strip()

            if val_str.lower() == "back":
                if scores:
                    # Undo last assignment
                    last_key = [a for a in abilities if a in scores][-1]
                    del scores[last_key]
                    self.console.print(f"  [yellow]Removed {last_key.capitalize()} assignment.[/yellow]")
                    continue
                return _BACK

            try:
                val = int(val_str)
                if val in available:
                    scores[next_ability] = val
                else:
                    self.console.print(f"  [red]{val} is not available.[/red]")
            except ValueError:
                self.console.print("  [red]Enter a number from the available list, or 'back'.[/red]")

    # --- Origin ---

    def _choose_origin(
        self,
        all_origins: list[dict],
        race: str,
        char_class: str,
        current: dict | None = None,
    ) -> Any:
        """Category browser for origin selection."""
        from text_rpg.content.loader import filter_origins

        available = filter_origins(all_origins, race, char_class)
        if not available:
            self.console.print("[dim]No origins available.[/dim]")
            return _BACK

        # Group by category
        categories: dict[str, list[dict]] = {}
        for origin in available:
            cat = origin.get("category", "other")
            categories.setdefault(cat, []).append(origin)

        cat_list = sorted(categories.keys())

        while True:
            self.console.print("\n[bold]Choose Your Origin[/bold]")
            self.console.print(f"[dim]{len(available)} origins available. Pick a category to browse, or 'back'.[/dim]\n")

            for i, cat in enumerate(cat_list, 1):
                origins_in_cat = categories[cat]
                label = cat.replace("_", " ").title()
                count = len(origins_in_cat)
                # Show if it's race/class specific
                sample = origins_in_cat[0]
                tag = ""
                if sample.get("required_races"):
                    tag = f" [yellow]({', '.join(r.title() for r in sample['required_races'])} only)[/yellow]"
                elif sample.get("required_classes"):
                    tag = f" [yellow]({', '.join(c.title() for c in sample['required_classes'])} only)[/yellow]"
                self.console.print(f"  [cyan]{i}.[/cyan] [bold]{label}[/bold] ({count} origins){tag}")

            choice = self.console.input("\n[bold cyan]Category > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK

            # Find category
            cat_found = None
            try:
                cat_idx = int(choice) - 1
                if 0 <= cat_idx < len(cat_list):
                    cat_found = cat_list[cat_idx]
            except ValueError:
                for cat in cat_list:
                    if cat.lower() == choice.lower().replace(" ", "_"):
                        cat_found = cat
                        break

            if not cat_found:
                self.console.print("[red]Invalid choice. Enter a category number.[/red]")
                continue

            # Show origins within category
            result = self._browse_category(categories[cat_found], cat_found)
            if result == _BACK:
                continue  # Go back to category list
            return result

    def _browse_category(self, origins: list[dict], category: str) -> Any:
        """Show origins within a category and let player pick one."""
        label = category.replace("_", " ").title()
        self.console.print(f"\n[bold]{label} Origins[/bold]")
        self.console.print("[dim]Type a number to select, ? to inspect (e.g. 1?), or 'back'[/dim]\n")

        for i, origin in enumerate(origins, 1):
            skills = origin.get("skill_proficiencies", [])
            skills_str = ", ".join(s.replace("_", " ").title() for s in skills)
            gold = origin.get("bonus_gold", 0)
            region = origin.get("starting_region", "").replace("_", " ").title()
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{origin['name']}[/bold]")
            self.console.print(f"     {origin.get('summary', '')}")
            self.console.print(f"     [dim]Skills: {skills_str} | +{gold}gp | {region}[/dim]")

        while True:
            choice = self.console.input("\n[bold cyan]Origin > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK

            if choice.endswith("?"):
                lookup = choice[:-1].strip()
                try:
                    idx = int(lookup) - 1
                    if 0 <= idx < len(origins):
                        self._inspect_origin(origins[idx])
                        continue
                except ValueError:
                    pass
                self.console.print("[red]Invalid. Try a number with ? (e.g. 1?)[/red]")
                continue

            found = self._find_by_index_or_name(origins, choice)
            if found:
                return found
            self.console.print("[red]Invalid choice. Enter a number or name.[/red]")

    def _inspect_origin(self, origin: dict) -> None:
        """Show full origin details panel."""
        content = f"[bold]{origin['name']}[/bold]\n"
        content += f"[dim]Category: {origin.get('category', '').replace('_', ' ').title()}[/dim]\n\n"
        content += f"{origin.get('summary', '')}\n"

        # Ability choices
        choices = origin.get("ability_choices", [])
        if choices:
            content += f"\n[bold]Ability Choices:[/bold] {', '.join(c[:3].upper() for c in choices)} (pick +2 and +1)"

        # Skills
        skills = origin.get("skill_proficiencies", [])
        if skills:
            content += f"\n[bold]Skill Proficiencies:[/bold] {', '.join(s.replace('_', ' ').title() for s in skills)}"

        # Equipment
        equip = origin.get("starting_equipment", [])
        if equip:
            content += f"\n[bold]Starting Gear:[/bold] {', '.join(e.replace('_', ' ').title() for e in equip)}"

        # Gold
        gold = origin.get("bonus_gold", 0)
        content += f"\n[bold]Bonus Gold:[/bold] {gold} gp"

        # Region
        region = origin.get("starting_region", "").replace("_", " ").title()
        location = origin.get("starting_location", "").replace("_", " ").title()
        content += f"\n[bold]Starting Location:[/bold] {location} ({region})"

        # Feature
        feat_name = origin.get("feature_name", "")
        feat_desc = origin.get("feature_description", "")
        if feat_name:
            content += f"\n\n[bold]Feature: {feat_name}[/bold]\n{feat_desc}"

        # Prologue preview
        prologue = origin.get("prologue", "")
        if prologue:
            # Show first paragraph only
            first_para = prologue.strip().split("\n\n")[0]
            content += f"\n\n[italic]{first_para}[/italic]"

        self.console.print(Panel(content, border_style="cyan", box=box.ROUNDED))

    def _assign_origin_bonuses(
        self, origin: dict,
        current_primary: str | None = None,
        current_secondary: str | None = None,
    ) -> Any:
        """Let player pick +2 and +1 from the origin's 3 ability choices."""
        choices = origin.get("ability_choices", [])
        if len(choices) < 2:
            return _BACK

        self.console.print(f"\n[bold]Origin Ability Bonuses[/bold] — {origin['name']}")
        self.console.print("[dim]Your origin allows +2 to one ability and +1 to another.[/dim]\n")

        for i, ability in enumerate(choices, 1):
            desc = ABILITY_DESCRIPTIONS.get(ability, "")
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{ability.capitalize()}[/bold]  [dim]{desc}[/dim]")

        # Pick +2
        self.console.print("\n[bold]Choose +2 bonus:[/bold]")
        while True:
            choice = self.console.input("[bold cyan]+2 > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK
            primary = self._resolve_ability_choice(choices, choice)
            if primary:
                break
            self.console.print("[red]Invalid. Pick a number or ability name from the list.[/red]")

        # Pick +1 (must be different from primary)
        remaining = [c for c in choices if c != primary]
        self.console.print("\n[bold]Choose +1 bonus:[/bold]")
        for i, ability in enumerate(remaining, 1):
            desc = ABILITY_DESCRIPTIONS.get(ability, "")
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{ability.capitalize()}[/bold]  [dim]{desc}[/dim]")
        while True:
            choice = self.console.input("[bold cyan]+1 > [/bold cyan]").strip()
            if choice.lower() == "back":
                return _BACK
            secondary = self._resolve_ability_choice(remaining, choice)
            if secondary:
                break
            self.console.print("[red]Invalid. Pick from the remaining abilities.[/red]")

        self.console.print(f"  [green]Origin bonuses: {primary.capitalize()} +2, {secondary.capitalize()} +1[/green]")
        return (primary, secondary)

    @staticmethod
    def _resolve_ability_choice(choices: list[str], input_str: str) -> str | None:
        """Resolve a player's ability choice by index or name."""
        try:
            idx = int(input_str) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            normalized = input_str.lower().strip()
            for c in choices:
                if c == normalized or c[:3] == normalized[:3]:
                    return c
        return None

    # --- Skills ---

    def _choose_skills(self, class_data: dict, current: list[str] | None = None, origin_skills: list[str] | None = None) -> Any:
        num_choices = class_data.get("skill_choices", 2)
        available = class_data.get("available_skills", [])

        # Show origin skills that are already granted (not selectable)
        granted: list[str] = []
        if origin_skills:
            for s in origin_skills:
                if s not in available:
                    granted.append(s)
                else:
                    # Origin skill overlaps with class list — player gets a replacement pick
                    num_choices += 1
            if granted:
                self.console.print(f"\n  [dim]Origin grants: {', '.join(s.replace('_', ' ').title() for s in granted)}[/dim]")

        self.console.print(f"\n[bold]Choose {num_choices} skills:[/bold]")
        self.console.print("[dim]Type a number to add, ? to inspect (e.g. 1?), 'back' to go back[/dim]\n")

        for i, skill in enumerate(available, 1):
            desc = SKILL_DESCRIPTIONS.get(skill, "")
            self.console.print(f"  [cyan]{i}.[/cyan] {skill.replace('_', ' ').title()}  [dim]{desc}[/dim]")

        chosen: list[str] = list(current) if current else []
        # Validate current choices still valid
        chosen = [s for s in chosen if s in available]

        while True:
            if len(chosen) >= num_choices:
                self.console.print(f"\n  [bold]Selected:[/bold] {', '.join(s.replace('_', ' ').title() for s in chosen)}")
                self.console.print("[dim]Commands: 'confirm', 'remove <#>', 'clear', 'back'[/dim]")
                cmd = self.console.input("\n[bold cyan]Skills > [/bold cyan]").strip().lower()
                if cmd == "back":
                    return _BACK
                if cmd == "confirm" or cmd == "done" or cmd == "y":
                    return chosen
                if cmd == "clear":
                    chosen = []
                    self.console.print("[yellow]Skills cleared.[/yellow]")
                    continue
                if cmd.startswith("remove"):
                    parts = cmd.split()
                    if len(parts) == 2:
                        try:
                            rm_idx = int(parts[1]) - 1
                            if 0 <= rm_idx < len(available) and available[rm_idx] in chosen:
                                chosen.remove(available[rm_idx])
                                self.console.print(f"  [yellow]Removed {available[rm_idx].replace('_', ' ').title()}.[/yellow]")
                                continue
                        except ValueError:
                            pass
                    self.console.print("[red]Usage: remove <#> (e.g. remove 2)[/red]")
                    continue
                self.console.print("[red]Type 'confirm', 'remove <#>', 'clear', or 'back'.[/red]")
                continue

            remaining = num_choices - len(chosen)
            if chosen:
                self.console.print(f"  [dim]Selected so far: {', '.join(s.replace('_', ' ').title() for s in chosen)}[/dim]")
            choice = self.console.input(f"\n[bold cyan]Skill ({remaining} remaining) > [/bold cyan]").strip()

            if choice.lower() == "back":
                if chosen:
                    removed = chosen.pop()
                    self.console.print(f"  [yellow]Removed {removed.replace('_', ' ').title()}.[/yellow]")
                    continue
                return _BACK

            if choice.endswith("?"):
                lookup = choice[:-1].strip()
                try:
                    skill_idx = int(lookup) - 1
                    if 0 <= skill_idx < len(available):
                        skill = available[skill_idx]
                        desc = SKILL_DESCRIPTIONS.get(skill, "No description available.")
                        self.console.print(Panel(
                            f"[bold]{skill.replace('_', ' ').title()}[/bold]\n{desc}",
                            border_style="cyan", box=box.ROUNDED,
                        ))
                    continue
                except ValueError:
                    continue

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(available) and available[idx] not in chosen:
                    chosen.append(available[idx])
                    self.console.print(f"  [green]Added: {available[idx].replace('_', ' ').title()}[/green]")
                elif 0 <= idx < len(available):
                    self.console.print("  [red]Already selected.[/red]")
                else:
                    self.console.print("  [red]Invalid choice.[/red]")
            except (ValueError, IndexError):
                for skill in available:
                    if skill.lower() == choice.lower().replace(" ", "_") and skill not in chosen:
                        chosen.append(skill)
                        self.console.print(f"  [green]Added: {skill.replace('_', ' ').title()}[/green]")
                        break
                else:
                    self.console.print("  [red]Invalid choice.[/red]")

    # --- Summary & Confirm ---

    def _show_summary_and_confirm(
        self, name: str, race: dict, char_class: dict,
        ability_scores: dict[str, int], skills: list[str],
        origin: dict | None = None,
        origin_primary: str | None = None,
        origin_secondary: str | None = None,
    ) -> str:
        self.console.print()
        table = Table(title="Character Summary", box=box.DOUBLE_EDGE, border_style="green")
        table.add_column("Attribute", style="bold")
        table.add_column("Value")
        table.add_row("[cyan]1.[/cyan] Name", name)
        table.add_row("[cyan]2.[/cyan] Race", race["name"])
        table.add_row("[cyan]3.[/cyan] Class", char_class["name"])
        table.add_row("", f"[dim]Hit Die: {char_class['hit_die']}[/dim]")
        if origin:
            table.add_row("[cyan]4.[/cyan] Origin", origin["name"])
            origin_bonuses = ""
            if origin_primary:
                origin_bonuses += f"{origin_primary[:3].upper()} +2"
            if origin_secondary:
                origin_bonuses += f", {origin_secondary[:3].upper()} +1"
            if origin_bonuses:
                table.add_row("", f"[dim]Bonuses: {origin_bonuses}[/dim]")
            origin_skills = origin.get("skill_proficiencies", [])
            if origin_skills:
                table.add_row("", f"[dim]Skills: {', '.join(s.replace('_', ' ').title() for s in origin_skills)}[/dim]")
        bonuses = race.get("ability_bonuses", {})
        # Build origin bonus map
        origin_bonus_map: dict[str, int] = {}
        if origin_primary:
            origin_bonus_map[origin_primary] = 2
        if origin_secondary:
            origin_bonus_map[origin_secondary] = origin_bonus_map.get(origin_secondary, 0) + 1
        abilities_header_shown = False
        ability_num = "5" if origin else "4"
        for ability, score in ability_scores.items():
            racial = bonuses.get(ability, 0)
            origin_b = origin_bonus_map.get(ability, 0)
            total = min(score + racial + origin_b, 20)
            mod = (total - 10) // 2
            mod_str = f"+{mod}" if mod >= 0 else str(mod)
            parts = []
            if racial:
                parts.append(f"[green]+{racial} racial[/green]")
            if origin_b:
                parts.append(f"[yellow]+{origin_b} origin[/yellow]")
            bonus_str = f" ({', '.join(parts)})" if parts else ""
            label = f"[cyan]{ability_num}.[/cyan] Abilities" if not abilities_header_shown else ""
            abilities_header_shown = True
            table.add_row(label, f"{ability.capitalize()}: {total} ({mod_str}){bonus_str}")
        skill_num = "6" if origin else "5"
        table.add_row(f"[cyan]{skill_num}.[/cyan] Skills", ", ".join(s.replace("_", " ").title() for s in skills))
        self.console.print(table)

        self.console.print("\n[dim]Commands: 'confirm' to create, 'edit' to change a section, 'back'[/dim]")
        while True:
            choice = self.console.input("\n[bold cyan]> [/bold cyan]").strip().lower()
            if choice in ("confirm", "y", "yes", "done"):
                return "confirm"
            if choice in ("edit", "change"):
                return "edit"
            if choice == "back":
                return _BACK
            self.console.print("[red]Type 'confirm', 'edit', or 'back'.[/red]")

    def _pick_stage_to_edit(self, has_origin: bool = False) -> int | None:
        self.console.print("\n[bold]Which section to edit?[/bold]")
        self.console.print("  [cyan]1.[/cyan] Name")
        self.console.print("  [cyan]2.[/cyan] Race")
        self.console.print("  [cyan]3.[/cyan] Class")
        if has_origin:
            self.console.print("  [cyan]4.[/cyan] Origin")
            self.console.print("  [cyan]5.[/cyan] Ability Scores")
            self.console.print("  [cyan]6.[/cyan] Skills")
            choice = self.console.input("\n[bold cyan]Section > [/bold cyan]").strip()
            mapping = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 5, "6": 6,
                       "name": 0, "race": 1, "class": 2, "origin": 3, "abilities": 5, "scores": 5, "skills": 6}
        else:
            self.console.print("  [cyan]4.[/cyan] Ability Scores")
            self.console.print("  [cyan]5.[/cyan] Skills")
            choice = self.console.input("\n[bold cyan]Section > [/bold cyan]").strip()
            mapping = {"1": 0, "2": 1, "3": 2, "4": 5, "5": 6,
                       "name": 0, "race": 1, "class": 2, "abilities": 5, "scores": 5, "skills": 6}
        return mapping.get(choice.lower())

    # --- Helpers ---

    @staticmethod
    def _find_by_index_or_name(items: list[dict], lookup: str) -> dict | None:
        try:
            idx = int(lookup) - 1
            if 0 <= idx < len(items):
                return items[idx]
        except ValueError:
            for item in items:
                if item.get("id", "").lower() == lookup.lower() or item.get("name", "").lower() == lookup.lower():
                    return item
        return None
