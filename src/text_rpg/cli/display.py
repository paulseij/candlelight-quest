"""Rich terminal display manager."""
from __future__ import annotations

import json
import urllib.request

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from text_rpg.utils import safe_json

console = Console()


class Display:
    def __init__(self, width: int = 80, show_dice: bool = True, show_mechanics: bool = True):
        self.console = console
        self.width = width
        self.show_dice = show_dice
        self.show_mechanics = show_mechanics

    def show_title_screen(self) -> None:
        title = Text()
        title.append("  _____ _____ __  __ _____   ____  ____   ____\n", style="bold cyan")
        title.append(" |_   _| ____|  \\/  |_   _| |  _ \\|  _ \\ / ___|\n", style="bold cyan")
        title.append("   | | |  _|  |\\/| | | |   | |_) | |_) | |  _\n", style="bold cyan")
        title.append("   | | | |___ | || | | |   |  _ <|  __/| |_| |\n", style="bold cyan")
        title.append("   |_| |_____|_|  |_| |_|   |_| \\_\\_|    \\____|\n", style="bold cyan")
        title.append("\n       A High Fantasy Text Adventure\n", style="dim")
        self.console.print(Panel(title, border_style="cyan", box=box.DOUBLE))

    def show_main_menu(self) -> str:
        self.console.print()
        self.console.print("[bold]Main Menu[/bold]", justify="center")
        self.console.print()
        self.console.print("  [cyan]1.[/cyan] New Game")
        self.console.print("  [cyan]2.[/cyan] Continue Game")
        self.console.print("  [cyan]3.[/cyan] Load Save")
        self.console.print("  [cyan]4.[/cyan] System Check")
        self.console.print("  [cyan]5.[/cyan] Quit")
        self.console.print()
        return self.console.input("[bold cyan]> [/bold cyan]").strip()

    def show_narrative(self, text: str) -> None:
        self.console.print()
        self.console.print(Panel(
            Markdown(text),
            border_style="green",
            box=box.ROUNDED,
            width=self.width,
            padding=(1, 2),
        ))

    def show_location(
        self,
        name: str,
        description: str,
        exits: list[dict] | None = None,
        entities: list[str] | None = None,
        items: list[str] | None = None,
    ) -> None:
        content = Text()
        content.append(f"{name}\n", style="bold yellow")
        content.append(f"{description}\n")
        if entities:
            content.append("\nPresent: ", style="bold")
            content.append(", ".join(entities))
        if items:
            content.append("\nItems: ", style="bold")
            content.append(", ".join(items))
        renderable = content
        if exits:
            content.append("\n\nExits:", style="bold")
            exit_table = Table.grid(padding=(0, 1), expand=True)
            exit_table.add_column(width=14, no_wrap=True, style="cyan bold")
            exit_table.add_column(ratio=1, overflow="fold")
            from text_rpg.engine.exits import exit_options
            for direction, exit_info in exit_options(exits):
                desc = exit_info.get("description", exit_info.get("target_location_id", "?"))
                exit_table.add_row(Text(f"  {direction}"), Text(str(desc)))
            for exit_info in exits:
                if not isinstance(exit_info, dict):
                    exit_table.add_row(Text(str(exit_info)), Text(""))
            numbered = [label for label, conn in exit_options(exits) if label != conn.get("direction", "").lower()]
            if numbered:
                exit_table.add_row(Text(""), Text(f"Choose a numbered path with go {numbered[0]}, for example.", style="dim"))
            renderable = Group(content, exit_table)
        self.console.print(Panel(renderable, border_style="yellow", box=box.ROUNDED, width=self.width))

    def show_dice_roll(
        self, expression: str, rolls: list[int], modifier: int, total: int, purpose: str = ""
    ) -> None:
        if not self.show_dice:
            return
        roll_str = ", ".join(str(r) for r in rolls)
        mod_str = f" + {modifier}" if modifier > 0 else (f" - {abs(modifier)}" if modifier < 0 else "")
        label = f" ({purpose})" if purpose else ""
        self.console.print(f"  [dim]Roll {expression}{label}: [{roll_str}]{mod_str} = [bold]{total}[/bold][/dim]")

    def show_combat_status(self, combatants: list[dict], current_turn: int) -> None:
        table = Table(title="Combat", box=box.SIMPLE_HEAVY, border_style="red")
        table.add_column("", width=3)
        table.add_column("Name", style="bold")
        table.add_column("HP", justify="right")
        table.add_column("AC", justify="right")
        table.add_column("Conditions")
        for i, c in enumerate(combatants):
            marker = "[bold red]>[/bold red]" if i == current_turn else " "
            hp = c.get("hp", {})
            hp_str = f"{hp.get('current', '?')}/{hp.get('max', '?')}" if isinstance(hp, dict) else str(hp)
            name_style = "green" if c.get("combatant_type") == "player" else "red"
            conditions = ", ".join(c.get("conditions", [])) or "-"
            table.add_row(marker, f"[{name_style}]{c.get('name', '?')}[/{name_style}]", hp_str, str(c.get("ac", "?")), conditions)
        self.console.print(table)

    def show_character_sheet(self, character: dict, equipped_items: dict | None = None, bounties: list[dict] | None = None) -> None:
        from rich.progress_bar import ProgressBar

        table = Table(title=f"{character['name']} - Character Sheet", box=box.DOUBLE_EDGE, border_style="cyan")
        table.add_column("Attribute", style="bold", width=20)
        table.add_column("Value", min_width=30)
        table.add_row("Race", str(character.get("race", "Unknown")).title())
        table.add_row("Class", str(character.get("char_class", "Unknown")).title())

        # Level with XP progress bar
        from text_rpg.mechanics.leveling import xp_for_level
        level = character.get("level", 1)
        current_xp = character.get("xp", 0)
        current_threshold = xp_for_level(level)
        next_threshold = xp_for_level(level + 1) if level < 20 else current_xp
        xp_in_level = current_xp - current_threshold
        xp_needed = max(next_threshold - current_threshold, 1)
        pct = min(xp_in_level / xp_needed, 1.0) if level < 20 else 1.0
        bar_width = 20
        filled = int(pct * bar_width)
        bar = f"[green]{'=' * filled}[/green][dim]{'-' * (bar_width - filled)}[/dim]"
        if level >= 20:
            table.add_row("Level", f"{level} [dim](MAX)[/dim]")
        else:
            table.add_row("Level", f"{level}    [{bar}] {current_xp}/{next_threshold} XP")

        hp_cur = character.get("hp_current", 0)
        hp_max = character.get("hp_max", 0)
        hp_pct = hp_cur / max(hp_max, 1)
        hp_color = "green" if hp_pct > 0.5 else ("yellow" if hp_pct > 0.25 else "red")
        table.add_row("HP", f"[{hp_color}]{hp_cur}[/{hp_color}]/{hp_max}")
        table.add_row("AC", str(character.get("ac", 10)))
        table.add_row("Speed", f"{character.get('speed', 30)} ft")
        table.add_row("Gold", f"[yellow]{character.get('gold', 0)}[/yellow] gp")

        # Equipped gear
        eq = equipped_items or {}
        weapon_name = eq.get("weapon_name", "Unarmed")
        armor_name = eq.get("armor_name", "None")
        weapon_dice = eq.get("weapon_dice", "")
        if weapon_dice:
            table.add_row("Weapon", f"{weapon_name} [dim]({weapon_dice})[/dim]")
        else:
            table.add_row("Weapon", weapon_name)
        table.add_row("Armor", armor_name)

        # Survival needs
        from text_rpg.mechanics.survival import classify_need
        has_needs = any(character.get(n) is not None for n in ("hunger", "thirst", "warmth", "morale"))
        if has_needs:
            first = True
            for need_name, icon in [("hunger", "Hunger"), ("thirst", "Thirst"), ("warmth", "Warmth"), ("morale", "Morale")]:
                val = character.get(need_name)
                if val is None:
                    continue
                status = classify_need(need_name, val)
                if val >= 75:
                    color = "green"
                elif val >= 50:
                    color = "yellow"
                elif val >= 25:
                    color = "dark_orange"
                else:
                    color = "red"
                bar_w = 10
                filled = int(val / 100 * bar_w)
                bar = f"[{color}]{'|' * filled}[/{color}][dim]{'.' * (bar_w - filled)}[/dim]"
                label = "Needs" if first else ""
                table.add_row(label, f"{icon:<8} [{bar}] [{color}]{status.label}[/{color}]")
                first = False

        # Ability scores
        scores = safe_json(character.get("ability_scores"), {})
        score_parts = []
        for ability in ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]:
            val = scores.get(ability, 10)
            mod = (val - 10) // 2
            mod_str = f"+{mod}" if mod >= 0 else str(mod)
            abbr = ability[:3].upper()
            score_parts.append(f"[bold]{abbr}[/bold] {val} ({mod_str})")
        table.add_row("Abilities", "  ".join(score_parts))

        skills = safe_json(character.get("skill_proficiencies"), [])
        if skills:
            table.add_row("Proficient Skills", ", ".join(s.replace("_", " ").title() for s in sorted(skills)))

        features = safe_json(character.get("class_features"), [])
        if features:
            table.add_row("Features", ", ".join(features))

        # Spell slots for spellcasters
        if character.get("spellcasting_ability"):
            slots_max = safe_json(character.get("spell_slots_max"), {})
            slots_remaining = safe_json(character.get("spell_slots_remaining"), {})
            if slots_max:
                slot_parts = []
                for lvl_key in sorted(slots_max, key=lambda k: int(k)):
                    remaining = int(slots_remaining.get(str(lvl_key), slots_remaining.get(int(lvl_key), 0)))
                    maximum = int(slots_max.get(str(lvl_key), slots_max.get(int(lvl_key), 0)))
                    bar = f"[green]{'O' * remaining}[/green][dim]{'.' * (maximum - remaining)}[/dim]"
                    slot_parts.append(f"Lv{lvl_key}:[{bar}]")
                table.add_row("Spell Slots", " ".join(slot_parts))
            conc = character.get("concentration_spell")
            if conc:
                table.add_row("Concentrating", f"[yellow]{conc}[/yellow]")

        conditions = safe_json(character.get("conditions"), [])
        if conditions:
            table.add_row("Conditions", ", ".join(conditions))

        # Active bounties summary
        if bounties:
            active_bounties = [b for b in bounties if b.get("amount", 0) > 0]
            if active_bounties:
                parts = []
                for b in active_bounties:
                    region = b.get("region", "?").replace("_", " ").title()
                    amount = b.get("amount", 0)
                    color = "yellow" if amount < 50 else ("dark_orange" if amount < 100 else "red")
                    parts.append(f"[{color}]{region}: {amount}g[/{color}]")
                table.add_row("Bounties", ", ".join(parts))

        self.console.print(table)

    def show_mechanical_summary(self, summary: str) -> None:
        if not self.show_mechanics:
            return
        self.console.print(f"  [dim italic]{summary}[/dim italic]")

    def show_help(self, context_mode: str = "exploration") -> None:
        """Show help commands appropriate to the current context.

        context_mode: "exploration", "combat", "conversation", "shop"
        """
        self.console.print()

        if context_mode == "combat":
            combat = Table(title="Combat Actions", box=box.SIMPLE, border_style="red", show_edge=False)
            combat.add_column("Command", style="cyan bold", width=22)
            combat.add_column("Description")
            combat.add_row("[1] / attack <target>", "Make a melee or ranged attack")
            combat.add_row("[2] / cast <spell>", "Cast a spell during combat")
            combat.add_row("[3] / use <item>", "Use an item during combat")
            combat.add_row("[4] / flee", "Attempt to flee (DEX check)")
            combat.add_row("[5] / dodge", "Impose disadvantage on attacks against you")
            combat.add_row("grapple <target>", "Contest Athletics to reduce a target's speed to zero")
            combat.add_row("dash", "Double your movement this turn")
            combat.add_row("disengage", "Move without provoking opportunity attacks")
            self.console.print(combat)
            self.console.print("\n[dim]Type 'character', 'inventory', or 'spells' for info during combat.[/dim]")
            return

        if context_mode == "conversation":
            conv = Table(title="Conversation", box=box.SIMPLE, border_style="magenta", show_edge=False)
            conv.add_column("Command", style="cyan bold", width=22)
            conv.add_column("Description")
            conv.add_row("<anything>", "Say something to the NPC")
            conv.add_row("goodbye / leave", "End the conversation")
            conv.add_row("give <item>", "Give an item to the NPC")
            conv.add_row("attack <npc>", "Attack (ends conversation)")
            self.console.print(conv)
            self.console.print("\n[dim]Just type what you want to say. The NPC will respond naturally.[/dim]")
            return

        if context_mode == "shop":
            shop = Table(title="Shop Commands", box=box.SIMPLE, border_style="yellow", show_edge=False)
            shop.add_column("Command", style="cyan bold", width=22)
            shop.add_column("Description")
            shop.add_row("browse / shop", "View the shop's wares and prices")
            shop.add_row("buy <item>", "Buy an item (gold deducted)")
            shop.add_row("sell <item>", "Sell an item (50% base value)")
            self.console.print(shop)
            self.console.print("\n[dim]Prices are affected by your reputation with the shop owner's faction.[/dim]")
            return

        # Default: exploration mode — show everything
        actions = Table(title="Actions", box=box.SIMPLE, border_style="blue", show_edge=False)
        actions.add_column("Command", style="cyan bold", width=22)
        actions.add_column("Description")
        actions.add_row("look", "Examine your surroundings")
        actions.add_row("options", "Show useful actions for the current scene")
        actions.add_row("go <direction>", "Move (north, south, east, west, etc.)")
        actions.add_row("talk to <npc>", "Talk to an NPC")
        actions.add_row("attack <target>", "Attack an enemy")
        actions.add_row("use <item>", "Use an item from your inventory")
        actions.add_row("equip <item>", "Equip a weapon or armor")
        actions.add_row("unequip <slot>", "Unequip weapon, armor, or all")
        actions.add_row("search", "Choose a feature to search, or search the area")
        actions.add_row("search behind <feature>", "Investigate a specific visible feature")
        actions.add_row("danger", "List established combat zones")
        actions.add_row("hunt", "Spend ten minutes seeking a hostile encounter")
        actions.add_row("craft <item>", "Craft an item using a known recipe")
        actions.add_row("train <skill>", "Learn a trade skill from a trainer")
        actions.add_row("cast <spell>", "Cast a spell (e.g. cast fire bolt at goblin)")
        actions.add_row("revival options", "View local ritual providers and costs")
        actions.add_row("revival <name> with <caster>", "Review a revival offer before paying")
        actions.add_row("confirm revival / cancel revival", "Accept or decline the reviewed ritual")
        actions.add_row("rest / rest short", "Short rest (spend hit dice to heal)")
        actions.add_row("rest long", "Long rest (full HP restore, 8 hours)")
        actions.add_row("browse / shop", "Browse a shop's wares")
        actions.add_row("buy <item>", "Buy an item from a shop")
        actions.add_row("sell <item>", "Sell an item to a shop")
        self.console.print(actions)

        # Info commands
        info = Table(title="Information", box=box.SIMPLE, border_style="green", show_edge=False)
        info.add_column("Command", style="cyan bold", width=22)
        info.add_column("Description")
        info.add_row("character / stats", "View your character sheet")
        info.add_row("skills", "View your skills and proficiencies")
        info.add_row("inventory / i", "View your inventory")
        info.add_row("quests / journal", "View your quest log")
        info.add_row("spells / spellbook", "View your known spells and slots")
        info.add_row("recipes / trade skills", "View trade skills and recipes")
        info.add_row("reputation / rep", "View faction standings")
        info.add_row("bounty", "View active bounties")
        info.add_row("stories", "View active story arcs")
        info.add_row("map", "View the world map")
        info.add_row("rewind", "Rewind time to a previous snapshot")
        info.add_row("save", "Save your game")
        info.add_row("help / ?", "Show this help")
        info.add_row("quit / exit", "Save and quit")
        self.console.print(info)

        self.console.print("\n[dim]Tip: You can also type natural language — the game will try to understand you.[/dim]")
        self.console.print("[dim]When talking to an NPC, you enter conversation mode — just type what you want to say.[/dim]")
        self.console.print("[dim]Say 'goodbye' or 'leave' to end a conversation.[/dim]")

    def show_skills(self, character: dict) -> None:
        """Show detailed skill proficiencies."""
        from text_rpg.mechanics.skills import SKILL_ABILITY_MAP
        from text_rpg.mechanics.ability_scores import modifier

        scores = safe_json(character.get("ability_scores"), {})

        proficient_skills = safe_json(character.get("skill_proficiencies"), [])

        prof_bonus = character.get("proficiency_bonus", 2)

        table = Table(
            title=f"{character.get('name', '?')} - Skills",
            box=box.ROUNDED, border_style="cyan",
        )
        table.add_column("Prof", width=4, justify="center")
        table.add_column("Skill", style="bold")
        table.add_column("Ability", style="dim")
        table.add_column("Mod", justify="center")

        for skill, ability in sorted(SKILL_ABILITY_MAP.items()):
            is_prof = skill in proficient_skills
            score = scores.get(ability, 10)
            mod = modifier(score)
            if is_prof:
                mod += prof_bonus
            mod_str = f"+{mod}" if mod >= 0 else str(mod)
            prof_mark = "[green]*[/green]" if is_prof else " "
            skill_name = skill.replace("_", " ").title()
            table.add_row(prof_mark, skill_name, ability[:3].upper(), mod_str)

        self.console.print(table)
        self.console.print(f"[dim]  * = proficient (proficiency bonus: +{prof_bonus})[/dim]")

    def show_prologue(self, text: str) -> None:
        """Display a story prologue with dramatic formatting."""
        self.console.print()
        paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
        for para in paragraphs:
            self.console.print(Panel(
                para,
                border_style="dim green",
                box=box.ROUNDED,
                width=self.width,
                padding=(1, 2),
            ))

    def show_how_to_play(self) -> None:
        """Show brief how-to-play instructions at game start."""
        content = (
            "[bold]How to Play[/bold]\n\n"
            "Type what you want to do in plain language, or use commands:\n\n"
            "  [cyan]look[/cyan]          Examine your surroundings\n"
            "  [cyan]options[/cyan]       Show actions that work in this scene\n"
            "  [cyan]go north[/cyan]      Move in a direction (n/s/e/w work too)\n"
            "  [cyan]talk to[/cyan] ...   Speak with NPCs\n"
            "  [cyan]attack[/cyan] ...    Fight enemies\n"
            "  [cyan]search[/cyan]        Look for hidden things\n"
            "  [cyan]inspect[/cyan] ...   Examine a visible feature without spending time\n"
            "  [cyan]pick up[/cyan] ...   Collect an item shown on the ground\n"
            "  [cyan]danger[/cyan]        List known combat zones\n"
            "  [cyan]hunt[/cyan]          Seek an encounter in a dangerous place\n"
            "  [cyan]inventory[/cyan]     Check your pack\n"
            "  [cyan]character[/cyan]     View your stats\n"
            "  [cyan]skills[/cyan]        View your skill proficiencies\n"
            "  [cyan]help[/cyan]          Full command list\n\n"
            "[dim]The game uses D&D 5e rules — dice rolls, ability checks, and combat.\n"
            "Your choices matter. Explore, fight, talk, and discover.[/dim]"
        )
        self.console.print(Panel(content, border_style="blue", box=box.ROUNDED, width=self.width))

    def show_origin_selection(self, origins: list[dict]) -> dict | None:
        """Let the player choose a starting scenario. Returns the chosen origin dict."""
        self.console.print("\n[bold]Choose Your Origin[/bold]")
        self.console.print("[dim]How does your story begin? Type a number to select, or ? to read more (e.g. 1?)[/dim]\n")
        for i, origin in enumerate(origins, 1):
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{origin['name']}[/bold]")
            self.console.print(f"     {origin['summary']}")

        while True:
            choice = self.console.input("\n[bold cyan]Origin > [/bold cyan]").strip()
            if choice.endswith("?"):
                try:
                    idx = int(choice[:-1]) - 1
                    if 0 <= idx < len(origins):
                        self.console.print(Panel(
                            f"[bold]{origins[idx]['name']}[/bold]\n\n{origins[idx]['prologue']}",
                            border_style="cyan", box=box.ROUNDED, width=self.width,
                        ))
                        continue
                except ValueError:
                    pass
                self.console.print("[red]Invalid. Try a number with ? (e.g. 1?)[/red]")
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(origins):
                    return origins[idx]
            except ValueError:
                for origin in origins:
                    if origin["name"].lower() == choice.lower() or origin["id"] == choice.lower():
                        return origin
            self.console.print("[red]Invalid choice. Enter a number.[/red]")

    def show_error(self, message: str) -> None:
        self.console.print(f"[bold red]Error:[/bold red] {message}")

    def show_info(self, message: str) -> None:
        self.console.print(f"[bold blue]Info:[/bold blue] {message}")

    def show_success(self, message: str) -> None:
        self.console.print(f"[bold green]{message}[/bold green]")

    def get_input(self, prompt: str = "> ") -> str:
        return self.console.input(f"[bold cyan]{prompt}[/bold cyan]").strip()

    def show_system_check(self) -> None:
        import tomllib
        from pathlib import Path
        from text_rpg.llm.env import load_env, get_api_key
        from text_rpg.llm.ollama_provider import DEFAULT_LOCAL_MODEL

        load_env()
        config_path = Path(__file__).parent.parent.parent.parent / "config.toml"
        config: dict = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
        llm_cfg = config.get("llm", {})
        provider = llm_cfg.get("provider", "ollama")
        model = llm_cfg.get("model", DEFAULT_LOCAL_MODEL)

        self.console.print("\n[bold]System Check[/bold]\n")
        self.console.print(f"Provider: [bold]{provider}[/bold]  Model: [bold]{model}[/bold]\n")

        if provider == "ollama":
            try:
                req = urllib.request.Request("http://localhost:11434/api/tags")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    models = [m.get("name", "") for m in data.get("models", [])]
                    self.console.print("[green]Ollama:[/green] Running")
                    for m in models:
                        self.console.print(f"  Model: {m}")
                    configured = model.removesuffix(":latest")
                    available = {m.removesuffix(":latest") for m in models}
                    if configured not in available:
                        self.console.print(
                            f"  [yellow]Warning: configured model '{model}' was not found. "
                            f"Run: ollama pull {model}[/yellow]"
                        )
                    if not any("nomic-embed-text" in m for m in models):
                        self.console.print("  [yellow]Warning: 'nomic-embed-text' not found. Run: ollama pull nomic-embed-text[/yellow]")
            except Exception:
                self.console.print("[red]Ollama:[/red] Not running. Start with: ollama serve")
        else:
            # Cloud provider — check API key
            key = get_api_key(provider)
            if key:
                masked = key[:4] + "..." + key[-4:]
                self.console.print(f"[green]{provider.title()} API key:[/green] {masked}")
            else:
                self.console.print(f"[red]{provider.title()} API key:[/red] Not set. Run [cyan]text-rpg setup[/cyan] to configure.")

        # RAG / embeddings status
        try:
            from text_rpg.app import GameApp

            game_app = GameApp()
            embeddings = game_app.embeddings
            reason = getattr(game_app, "rag_disabled_reason", None)
            tag = getattr(embeddings, "model_tag", "?")
            if reason:
                self.console.print(f"[yellow]RAG memory:[/yellow] Disabled — {reason}")
            else:
                self.console.print(f"[green]RAG memory:[/green] {tag}")
        except Exception as e:
            self.console.print(f"[yellow]RAG memory:[/yellow] Could not check ({e})")

        for pkg in ["pydantic", "litellm", "chromadb", "rich", "typer", "jinja2"]:
            try:
                __import__(pkg)
                self.console.print(f"[green]{pkg}:[/green] Installed")
            except ImportError:
                self.console.print(f"[red]{pkg}:[/red] Not installed")
        self.console.print()

    def show_saves_list(self, saves: list[dict]) -> None:
        if not saves:
            self.console.print("[dim]No saved games found.[/dim]")
            return
        table = Table(title="Saved Games", box=box.ROUNDED, border_style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Character")
        table.add_column("Turn")
        table.add_column("Created")
        for s in saves:
            table.add_row(s.get("name", "?"), s.get("character_name", "?"), str(s.get("turn_number", 0)), s.get("created_at", "?")[:10])
        self.console.print(table)

    def show_quest_notification(self, quest_name: str, notification_type: str = "new") -> None:
        """Show a prominent quest notification."""
        if notification_type == "new":
            self.console.print()
            self.console.print(Panel(
                f"[bold yellow]New Quest[/bold yellow]\n{quest_name}",
                border_style="yellow",
                box=box.DOUBLE,
                width=min(self.width, 50),
                padding=(0, 2),
            ))
        elif notification_type == "completed":
            self.console.print()
            self.console.print(Panel(
                f"[bold green]Quest Completed![/bold green]\n{quest_name}",
                border_style="green",
                box=box.DOUBLE,
                width=min(self.width, 50),
                padding=(0, 2),
            ))
        elif notification_type == "updated":
            self.console.print(f"\n  [dim italic]Quest Updated: {quest_name}[/dim italic]")

    def show_quest_log(self, quests: list[dict]) -> None:
        if not quests:
            self.console.print("[dim]No quests yet. Talk to NPCs to discover quests.[/dim]")
            return

        # Group by status
        active = [q for q in quests if q.get("status") == "active"]
        completed = [q for q in quests if q.get("status") == "completed"]
        failed = [q for q in quests if q.get("status") == "failed"]

        if active:
            self.console.print("\n[bold yellow]Active Quests[/bold yellow]")
            for q in active:
                self._show_quest_entry(q, "yellow")

        if completed:
            self.console.print("\n[bold green]Completed Quests[/bold green]")
            for q in completed:
                self._show_quest_entry(q, "green")

        if failed:
            self.console.print("\n[bold red]Failed Quests[/bold red]")
            for q in failed:
                self._show_quest_entry(q, "red")

    def _show_quest_entry(self, quest: dict, color: str) -> None:
        """Display a single quest with objectives and rewards."""
        name = quest.get("name", "Unknown Quest")
        desc = quest.get("description", "")
        self.console.print(f"\n  [{color} bold]{name}[/{color} bold]")
        if desc:
            self.console.print(f"  [dim]{desc}[/dim]")

        objectives = quest.get("objectives", [])
        if objectives:
            for obj in objectives:
                if isinstance(obj, str):
                    obj = json.loads(obj) if obj.startswith("{") else {"description": obj}
                is_done = obj.get("is_complete", False)
                check = "[green]x[/green]" if is_done else "[dim]o[/dim]"
                obj_desc = obj.get("description", "")
                # Show progress count if applicable
                required = obj.get("required_count", 1)
                current = obj.get("current_count", 0)
                if required > 1:
                    progress = f" ({current}/{required})"
                    obj_desc += progress
                style = "dim" if is_done else ""
                self.console.print(f"    [{check}] [{style}]{obj_desc}[/{style}]" if style else f"    [{check}] {obj_desc}")

        # Show rewards
        rewards = []
        xp = quest.get("xp_reward", 0)
        if xp:
            rewards.append(f"{xp} XP")
        gold = quest.get("gold_reward", 0)
        if gold:
            rewards.append(f"{gold} gold")
        item_rewards = quest.get("item_rewards", [])
        if item_rewards:
            rewards.extend(item_rewards)
        if rewards:
            self.console.print(f"    [dim]Rewards: {', '.join(rewards)}[/dim]")

    def show_level_up(self, new_level: int, hp_gained: int, new_features: list[str] | None = None) -> None:
        """Show a prominent level-up notification panel."""
        content = f"[bold yellow]LEVEL UP![/bold yellow]\n\nYou are now [bold]level {new_level}[/bold]!"
        content += f"\n[green]+{hp_gained} HP[/green]"
        if new_features:
            content += "\n\n[bold]New Features:[/bold]"
            for feat in new_features:
                content += f"\n  [cyan]{feat}[/cyan]"
        self.console.print()
        self.console.print(Panel(
            content,
            border_style="yellow",
            box=box.DOUBLE,
            width=min(self.width, 50),
            padding=(1, 2),
            title="[bold]Level Up[/bold]",
        ))

    def show_inventory(
        self,
        items: list[dict],
        all_items_data: dict,
        gold: int = 0,
        equipped_weapon_id: str | None = None,
        equipped_armor_id: str | None = None,
        category_filter: str | None = None,
        sort_by: str = "name",
        sort_desc: bool = False,
        carry_weight: float | None = None,
        max_carry_weight: float | None = None,
    ) -> None:
        """Show a rich, sortable, filterable inventory display."""
        self.console.print()

        # Header with gold and carry weight
        header_parts = [f"[yellow]{gold} gp[/yellow]"]
        if carry_weight is not None and max_carry_weight is not None:
            weight_pct = carry_weight / max(max_carry_weight, 1)
            weight_color = "green" if weight_pct < 0.67 else ("yellow" if weight_pct < 1.0 else "red")
            header_parts.append(f"[{weight_color}]{carry_weight:.1f}/{max_carry_weight:.0f} lbs[/{weight_color}]")
        header = "  ".join(header_parts)

        table = Table(
            title="Inventory",
            caption=header,
            box=box.ROUNDED,
            border_style="cyan",
            show_lines=False,
        )
        table.add_column("", width=3, justify="center")  # Equipped marker
        table.add_column("Item", style="bold", min_width=20)
        table.add_column("Type", style="dim", width=10)
        table.add_column("Qty", justify="right", width=4)
        table.add_column("Weight", justify="right", width=8)
        table.add_column("Value", justify="right", width=8)

        # Build enriched item list
        enriched: list[dict] = []
        for entry in items:
            item_id = entry.get("item_id", "")
            qty = entry.get("quantity", 1)
            item_data = all_items_data.get(item_id, {})
            name = item_data.get("name", item_id.replace("_", " ").title())
            item_type = item_data.get("item_type", "misc")
            weight = item_data.get("weight", 0.0) * qty
            value = item_data.get("value_gp", 0)
            enriched.append({
                "item_id": item_id, "name": name, "item_type": item_type,
                "qty": qty, "weight": weight, "unit_weight": item_data.get("weight", 0.0),
                "value": value, "is_weapon": item_type == "weapon",
                "is_armor": item_type == "armor",
            })

        # Filter by category
        if category_filter and category_filter != "all":
            enriched = [e for e in enriched if e["item_type"] == category_filter]

        # Sort
        sort_key = {
            "name": lambda x: x["name"].lower(),
            "type": lambda x: x["item_type"],
            "weight": lambda x: x["weight"],
            "value": lambda x: x["value"],
        }.get(sort_by, lambda x: x["name"].lower())
        enriched.sort(key=sort_key, reverse=sort_desc)

        if not enriched:
            if category_filter and category_filter != "all":
                self.console.print(f"[dim]No {category_filter} items in your inventory.[/dim]")
            else:
                self.console.print("[dim]Your pack is empty.[/dim]")
            return

        for e in enriched:
            equipped = ""
            if e["item_id"] == equipped_weapon_id:
                equipped = "[green]E[/green]"
            elif e["item_id"] == equipped_armor_id:
                equipped = "[green]E[/green]"

            type_display = e["item_type"].replace("_", " ").title()
            weight_str = f"{e['unit_weight']:.1f}" if e["qty"] == 1 else f"{e['unit_weight']:.1f}x{e['qty']}"
            value_str = f"{e['value']} gp" if e["value"] > 0 else "-"

            table.add_row(equipped, e["name"], type_display, str(e["qty"]), weight_str, value_str)

        self.console.print(table)

    def show_spells(
        self,
        known_spells: list[dict],
        prepared_ids: list[str],
        slots_remaining: dict,
        slots_max: dict,
        concentration_spell: str | None = None,
    ) -> None:
        """Show known spells grouped by level with slot bars."""
        self.console.print()

        if not known_spells:
            self.console.print("[dim]You don't know any spells.[/dim]")
            return

        # Spell slot bars
        if slots_max:
            slot_parts = []
            for level in sorted(int(k) for k in slots_max):
                remaining = int(slots_remaining.get(str(level), slots_remaining.get(level, 0)))
                maximum = int(slots_max.get(str(level), slots_max.get(level, 0)))
                filled = remaining
                empty = maximum - remaining
                bar = f"[green]{'O' * filled}[/green][dim]{'.' * empty}[/dim]"
                slot_parts.append(f"Lv{level}: [{bar}] {remaining}/{maximum}")
            self.console.print(f"  [bold]Spell Slots:[/bold]  {'   '.join(slot_parts)}")
            self.console.print()

        if concentration_spell:
            self.console.print(f"  [bold yellow]Concentrating on:[/bold yellow] {concentration_spell}")
            self.console.print()

        # Group by level
        by_level: dict[int, list[dict]] = {}
        for spell in known_spells:
            lvl = spell.get("level", 0)
            by_level.setdefault(lvl, []).append(spell)

        for level in sorted(by_level):
            level_name = "Cantrips" if level == 0 else f"Level {level}"
            self.console.print(f"  [bold cyan]{level_name}[/bold cyan]")
            for spell in sorted(by_level[level], key=lambda s: s["name"]):
                is_prepared = spell["id"] in prepared_ids
                prep_mark = "[green][P][/green]" if is_prepared else "[dim][ ][/dim]"
                conc = " [yellow](C)[/yellow]" if spell.get("concentration") else ""
                mech = spell.get("mechanics", {})
                detail = ""
                if mech.get("damage_dice"):
                    detail = f" [dim]({mech['damage_dice']} {mech.get('damage_type', '')})[/dim]"
                elif mech.get("healing_dice"):
                    detail = f" [dim]({mech['healing_dice']} healing)[/dim]"
                elif mech.get("effect"):
                    detail = f" [dim]({mech['effect']})[/dim]"
                self.console.print(f"    {prep_mark} {spell['name']}{conc}{detail}")
            self.console.print()

        self.console.print("[dim]  [P] = prepared, (C) = concentration[/dim]")

    def show_trade_skills(self, skills: list[dict], known_recipes: list[dict]) -> None:
        """Show trade skills and known recipes."""
        from text_rpg.mechanics.crafting import (
            RECIPES, TRADE_SKILL_DESCRIPTIONS, TRADE_SKILL_XP,
            get_available_recipes,
        )

        self.console.print()

        if not skills:
            self.console.print("[dim]You haven't learned any trade skills yet.[/dim]")
            self.console.print("[dim]Find a trainer NPC or use 'train <skill>' to learn.[/dim]")
            self.console.print(f"[dim]Available skills: {', '.join(TRADE_SKILL_DESCRIPTIONS.keys())}[/dim]")
            return

        known_recipe_ids = {r.get("recipe_id") for r in known_recipes}

        for skill in skills:
            if not skill.get("is_learned"):
                continue
            name = skill["skill_name"]
            level = skill.get("level", 1)
            xp = skill.get("xp", 0)
            desc = TRADE_SKILL_DESCRIPTIONS.get(name, "")

            # XP progress
            current_threshold = TRADE_SKILL_XP.get(level, 0)
            next_threshold = TRADE_SKILL_XP.get(level + 1, xp) if level < 10 else xp
            xp_in_level = xp - current_threshold
            xp_needed = max(next_threshold - current_threshold, 1)
            pct = min(xp_in_level / xp_needed, 1.0) if level < 10 else 1.0
            bar_w = 15
            filled = int(pct * bar_w)
            bar = f"[green]{'=' * filled}[/green][dim]{'-' * (bar_w - filled)}[/dim]"

            self.console.print(f"\n  [bold cyan]{name.title()}[/bold cyan] [dim]— {desc}[/dim]")
            if level >= 10:
                self.console.print(f"    Level {level} [dim](MAX)[/dim]")
            else:
                self.console.print(f"    Level {level}  [{bar}] {xp}/{next_threshold} XP")

            # Show available recipes
            available = get_available_recipes(name, level)
            if available:
                self.console.print("    [bold]Recipes:[/bold]")
                for recipe in available:
                    known = recipe.id in known_recipe_ids
                    known_mark = "[green]*[/green]" if known else "[dim]?[/dim]"
                    mats = ", ".join(f"{q}x {m.replace('_', ' ')}" for m, q in recipe.materials.items()) if recipe.materials else "none"
                    self.console.print(f"      {known_mark} {recipe.name} [dim](DC {recipe.dc}, needs: {mats})[/dim]")

        self.console.print(f"\n[dim]  * = known recipe, ? = not yet learned[/dim]")
        self.console.print(f"[dim]  Use 'craft <recipe>' to craft, 'train <skill>' to learn new skills[/dim]")

    def show_reputation(self, faction_reps: dict[str, int], factions_data: dict[str, dict]) -> None:
        """Show faction reputation standings with tier names and bars."""
        from text_rpg.mechanics.reputation import get_tier

        self.console.print()
        if not faction_reps and not factions_data:
            self.console.print("[dim]No faction standings yet.[/dim]")
            return

        table = Table(title="Faction Standings", box=box.ROUNDED, border_style="cyan")
        table.add_column("Faction", style="bold", min_width=25)
        table.add_column("Rep", justify="right", width=5)
        table.add_column("Standing", width=12)
        table.add_column("", min_width=22)  # Bar

        # Show all factions, even if rep is default 0
        all_faction_ids = set(list(faction_reps.keys()) + list(factions_data.keys()))
        for fid in sorted(all_faction_ids):
            rep = faction_reps.get(fid, 0)
            faction = factions_data.get(fid, {})
            name = faction.get("name", fid.replace("_", " ").title())
            tier = get_tier(rep)

            # Color based on tier
            tier_colors = {
                "hated": "bold red",
                "hostile": "red",
                "unfriendly": "dark_orange",
                "neutral": "white",
                "friendly": "green",
                "trusted": "bold green",
                "honored": "bold yellow",
            }
            color = tier_colors.get(tier, "white")

            # Bar: map -100..+100 to 0..20 chars
            bar_w = 20
            normalized = (rep + 100) / 200  # 0.0 to 1.0
            filled = int(normalized * bar_w)
            if rep > 0:
                bar = f"[dim]{'.' * 10}[/dim][green]{'|' * (filled - 10)}[/green][dim]{'.' * (bar_w - filled)}[/dim]"
            elif rep < 0:
                bar = f"[red]{'|' * filled}[/red][dim]{'.' * (10 - filled)}[/dim][dim]{'.' * 10}[/dim]"
            else:
                bar = f"[dim]{'.' * 10}[/dim][white]|[/white][dim]{'.' * 9}[/dim]"

            table.add_row(name, str(rep), f"[{color}]{tier.title()}[/{color}]", f"[{bar}]")

        self.console.print(table)

    def show_journal(self, active_stories: list[dict], completed_stories: list[str], all_seeds: dict[str, dict]) -> None:
        """Show story journal — active arcs and completed stories."""
        self.console.print()

        if not active_stories and not completed_stories:
            self.console.print("[dim]No story arcs yet. Explore the world and they will find you.[/dim]")
            return

        beat_labels = {
            "hook": "Emerging",
            "development": "Developing",
            "escalation": "Escalating",
            "resolution": "Climax",
        }

        if active_stories:
            self.console.print("[bold cyan]Active Story Arcs[/bold cyan]")
            for story in active_stories:
                seed_id = story.get("seed_id", "")
                seed = all_seeds.get(seed_id, {})
                name = seed.get("name", seed_id.replace("_", " ").title())
                category = seed.get("category", "unknown").title()
                current_beat = story.get("current_beat", "hook")
                beat_label = beat_labels.get(current_beat, current_beat.title())

                # Beat progress bar
                beat_order = ["hook", "development", "escalation", "resolution"]
                try:
                    idx = beat_order.index(current_beat)
                except ValueError:
                    idx = 0
                progress = "".join(
                    f"[green]=[/green]" if i <= idx else "[dim]-[/dim]"
                    for i in range(4)
                )

                # Resolve description template
                variables = safe_json(story.get("resolved_variables"), {})
                desc_template = seed.get("description_template", "")
                for key, val in variables.items():
                    desc_template = desc_template.replace(f"{{{key}}}", str(val))

                self.console.print(f"\n  [bold yellow]{name}[/bold yellow] [dim]({category})[/dim]")
                if desc_template:
                    self.console.print(f"  [dim]{desc_template}[/dim]")
                self.console.print(f"    Stage: [{progress}] [cyan]{beat_label}[/cyan]")

        if completed_stories:
            self.console.print(f"\n[bold green]Completed Stories[/bold green]")
            for seed_id in completed_stories:
                seed = all_seeds.get(seed_id, {})
                name = seed.get("name", seed_id.replace("_", " ").title())
                self.console.print(f"  [green]x[/green] {name}")

    def show_story_notification(self, story_name: str, beat_name: str) -> None:
        """Show a story progression notification."""
        beat_labels = {
            "hook": "A new story emerges",
            "development": "The story deepens",
            "escalation": "Events escalate",
            "resolution": "The climax approaches",
        }
        label = beat_labels.get(beat_name, "Story update")
        self.console.print()
        self.console.print(Panel(
            f"[bold magenta]{label}[/bold magenta]\n{story_name}",
            border_style="magenta",
            box=box.DOUBLE,
            width=min(self.width, 50),
            padding=(0, 2),
        ))

    def show_map(self, locations: list[dict], current_location_id: str) -> None:
        """Show an ASCII map of visited and connected locations."""
        self.console.print()

        if not locations:
            self.console.print("[dim]No map data available.[/dim]")
            return

        # Build adjacency from location connections
        loc_map: dict[str, dict] = {}
        for loc in locations:
            loc_id = loc.get("id", "")
            loc_map[loc_id] = loc

        # BFS from current location to gather connected nodes
        visited_ids = {loc["id"] for loc in locations if loc.get("visited")}
        current = loc_map.get(current_location_id)
        if not current:
            self.console.print("[dim]Map unavailable for current location.[/dim]")
            return

        # Simple text-based map: show current + connected
        content = Text()
        content.append("World Map\n\n", style="bold cyan")

        # Show current location at center
        cur_name = current.get("name", current_location_id)
        content.append(f"  [*{cur_name}*]  (You are here)\n", style="bold green")

        connections = safe_json(current.get("connections"), [])

        if connections:
            content.append("        |\n", style="dim")
            for conn in connections:
                if isinstance(conn, dict):
                    target_id = conn.get("target_location_id", "")
                    direction = conn.get("direction", "?")
                    is_locked = conn.get("is_locked", False)
                    target_loc = loc_map.get(target_id)
                    if target_loc and target_id in visited_ids:
                        target_name = target_loc.get("name", target_id)
                        marker = f"[{target_name}]"
                    else:
                        marker = "[???]"
                    if is_locked:
                        marker += " [locked]"
                    content.append(f"    {direction:<12} ", style="cyan")
                    if target_id in visited_ids:
                        content.append(f"{marker}\n", style="yellow")
                    else:
                        content.append(f"{marker}\n", style="dim")

        # Show other visited locations not directly connected
        connected_ids = {c.get("target_location_id", "") for c in connections if isinstance(c, dict)}
        other_visited = [lid for lid in visited_ids if lid != current_location_id and lid not in connected_ids]
        if other_visited:
            content.append("\n  Other visited locations:\n", style="bold")
            for lid in other_visited:
                loc = loc_map.get(lid)
                if loc:
                    content.append(f"    [{loc.get('name', lid)}]\n", style="dim yellow")

        self.console.print(Panel(content, border_style="cyan", box=box.ROUNDED, width=self.width))

    def show_bounty(self, bounties: list[dict]) -> None:
        """Show active bounties by region."""
        self.console.print()
        active = [b for b in bounties if b.get("amount", 0) > 0]
        if not active:
            self.console.print("[dim]You have no active bounties. You're a law-abiding citizen.[/dim]")
            return

        for b in active:
            region = b.get("region", "Unknown").replace("_", " ").title()
            amount = b.get("amount", 0)
            crimes = b.get("crimes", [])

            color = "yellow" if amount < 50 else ("dark_orange" if amount < 100 else "red")
            content = f"[{color} bold]WANTED[/{color} bold] in {region}\n"
            content += f"Bounty: [{color}]{amount} gold[/{color}]\n"
            if crimes:
                content += "\nCrimes:\n"
                for crime in crimes[-5:]:  # Show last 5 crimes
                    content += f"  - {crime}\n"

            self.console.print(Panel(
                content.strip(),
                border_style=color,
                box=box.ROUNDED,
                width=min(self.width, 50),
            ))
