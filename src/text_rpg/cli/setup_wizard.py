"""Interactive setup wizard for choosing an LLM provider and model."""
from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from text_rpg.llm.env import get_api_key, load_env, save_api_key
from text_rpg.llm.models import MODELS, ModelInfo, infer_provider

console = Console()
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config.toml"


def run_setup_wizard() -> None:
    """Walk the user through model selection and API key configuration."""
    load_env()

    console.print("\n[bold]LLM Setup Wizard[/bold]\n")
    console.print("Choose a model for generating game narrative.\n")

    # -- Model table --
    table = Table(box=box.ROUNDED, border_style="cyan")
    table.add_column("#", justify="right", width=3)
    table.add_column("Model", style="bold", min_width=20)
    table.add_column("Provider", width=10)
    table.add_column("Cost", width=18)
    table.add_column("Quality", width=10)
    table.add_column("Notes", min_width=20)

    for i, m in enumerate(MODELS, 1):
        table.add_row(str(i), m.name, m.provider.title(), m.cost, m.quality, m.notes)

    console.print(table)

    custom_num = len(MODELS) + 1
    console.print(f"  [cyan]{custom_num}.[/cyan] [bold]Custom model[/bold] (enter any LiteLLM model ID)")

    # -- Selection --
    while True:
        choice = console.input(f"\n[bold cyan]Pick a model (1-{custom_num}) > [/bold cyan]").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx <= len(MODELS):
                break
        except ValueError:
            pass
        console.print(f"[red]Enter a number between 1 and {custom_num}.[/red]")

    if idx < len(MODELS):
        selected = MODELS[idx]
        console.print(f"\nSelected: [bold]{selected.name}[/bold] ({selected.provider})")
    else:
        # Custom model
        model_id = console.input("\n[bold cyan]Enter model ID (e.g. gpt-5-nano) > [/bold cyan]").strip()
        if not model_id:
            console.print("[red]No model entered. Aborting.[/red]")
            return
        provider = infer_provider(model_id)
        console.print(f"[dim]Detected provider: {provider}[/dim]")
        override = console.input(f"Provider ({provider})? Press Enter to confirm, or type a different one > ").strip()
        if override:
            provider = override.lower()

        env_var_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        env_var = env_var_map.get(provider)
        selected = ModelInfo(
            id=model_id,
            provider=provider,
            name=model_id,
            cost="Unknown",
            quality="Unknown",
            notes="Custom model",
            requires_key=provider != "ollama",
            env_var=env_var,
        )
        console.print(f"\nSelected: [bold]{model_id}[/bold] ({provider})")

    # -- API key handling --
    if selected.requires_key:
        existing = get_api_key(selected.provider)
        if existing:
            masked = existing[:4] + "..." + existing[-4:]
            console.print(f"[green]API key found:[/green] {masked}")
            reuse = console.input("Use this key? (Y/n) > ").strip().lower()
            if reuse in ("n", "no"):
                existing = None

        if not existing:
            console.print(f"\n[bold]Enter your {selected.provider.title()} API key[/bold]")
            console.print(f"[dim]This will be saved to .env (git-ignored)[/dim]")
            key = console.input(f"{selected.env_var}= ").strip()
            if not key:
                console.print("[red]No key entered. Aborting.[/red]")
                return
            save_api_key(selected.provider, key)
            console.print("[green]API key saved to .env[/green]")

    # -- Update config.toml --
    _update_config(selected.provider, selected.id)
    console.print(f"[green]config.toml updated:[/green] provider={selected.provider}, model={selected.id}")
    console.print("\n[bold green]Setup complete![/bold green] Run [cyan]text-rpg play[/cyan] to start.\n")


def _update_config(provider: str, model_id: str) -> None:
    """Update the [llm] section of config.toml with new provider and model."""
    if not _CONFIG_PATH.exists():
        return

    lines = _CONFIG_PATH.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    provider_updated = False
    model_updated = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("provider"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                indent = line[: len(line) - len(stripped)]
                # Preserve inline comment if present
                value_part = parts[1]
                comment = ""
                if "#" in value_part:
                    val, _, comment = value_part.partition("#")
                    comment = "  # " + comment.strip()
                new_lines.append(f'{indent}provider = "{provider}"{comment}\n')
                provider_updated = True
                continue
        if stripped.startswith("model") and not stripped.startswith("model_"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                indent = line[: len(line) - len(stripped)]
                value_part = parts[1]
                comment = ""
                if "#" in value_part:
                    val, _, comment = value_part.partition("#")
                    comment = "  # " + comment.strip()
                new_lines.append(f'{indent}model = "{model_id}"{comment}\n')
                model_updated = True
                continue
        new_lines.append(line)

    _CONFIG_PATH.write_text("".join(new_lines))
