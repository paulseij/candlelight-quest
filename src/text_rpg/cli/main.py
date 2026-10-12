"""Typer CLI application."""
from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(
    name="text-rpg",
    help="A D&D-like high fantasy text RPG powered by LLMs",
    no_args_is_help=False,
)


@app.command()
def play(
    save_name: Optional[str] = typer.Option(None, "--save", "-s", help="Load a saved game"),
    new: bool = typer.Option(False, "--new", "-n", help="Start a new game"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="LLM model to use"),
) -> None:
    """Start or continue your adventure."""
    from text_rpg.app import GameApp

    game_app = GameApp(model_override=model)
    if new:
        game_app.new_game()
    elif save_name:
        game_app.load_game(save_name)
    else:
        game_app.main_menu()


@app.command()
def saves() -> None:
    """List all saved games."""
    from text_rpg.app import GameApp

    game_app = GameApp()
    game_app.list_saves()


@app.command()
def check() -> None:
    """Check system requirements (Ollama, models, etc)."""
    from text_rpg.cli.display import Display

    display = Display()
    display.show_system_check()


@app.command()
def reindex(
    save_name: Optional[str] = typer.Option(None, "--save", "-s", help="Save to reindex (default: all)"),
) -> None:
    """Rebuild the GM's memory (RAG vectors) from the events ledger."""
    from text_rpg.app import GameApp

    game_app = GameApp()
    repos = game_app._get_repos()
    games = repos["save_game"].list_games()
    if save_name:
        games = [g for g in games if g.get("name") == save_name]
        if not games:
            typer.echo(f"No save named '{save_name}' found.")
            raise typer.Exit(1)
    if not game_app.indexer.is_available:
        reason = getattr(game_app, "rag_disabled_reason", None) or "embedding backend unreachable"
        typer.echo(f"Cannot reindex: {reason}")
        raise typer.Exit(1)
    for game in games:
        count = game_app.indexer.reindex_from_db(repos, game["id"])
        typer.echo(f"Reindexed {count} documents for '{game.get('name', game['id'])}'.")


@app.command()
def setup() -> None:
    """Choose your LLM provider and model."""
    from text_rpg.cli.setup_wizard import run_setup_wizard

    run_setup_wizard()


if __name__ == "__main__":
    app()
