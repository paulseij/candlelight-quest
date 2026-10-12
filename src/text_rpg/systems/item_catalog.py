"""Item catalog with player-invented items overlaid on the TOML catalog."""
from __future__ import annotations

import logging
from typing import Any

from text_rpg.content.loader import load_all_items
from text_rpg.storage.repos.custom_item_repo import CustomItemRepo

logger = logging.getLogger(__name__)


def get_item_catalog(
    repos: dict[str, Any] | None,
    game_id: str | None,
    character_id: str | None,
) -> dict[str, dict]:
    """The full item catalog: authored TOML items plus this character's
    custom (player-invented) items in the same shape."""
    items = dict(load_all_items())
    if not repos or not game_id or not character_id:
        return items
    repo = repos.get("custom_item")
    if not repo:
        return items
    try:
        for row in repo.get_custom_items(game_id, character_id):
            items[row["id"]] = CustomItemRepo.to_catalog_entry(row)
    except Exception as e:
        logger.warning(f"Failed to overlay custom items: {e}")
    return items
