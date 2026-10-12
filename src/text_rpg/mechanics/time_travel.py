"""Time travel mechanics — persistence rules and restore configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RestoreConfig:
    """What persists across a time rewind."""
    keep_player_stats: bool = True
    keep_inventory: bool = True
    keep_spells: bool = True
    keep_reputation: bool = False
    keep_bounties: bool = False
    keep_companions: bool = False


# Preset configurations for different trigger types.
RESTORE_PRESETS: dict[str, RestoreConfig] = {
    "artifact": RestoreConfig(
        keep_player_stats=True, keep_inventory=True, keep_spells=True,
    ),
    "death": RestoreConfig(
        keep_player_stats=True, keep_inventory=False, keep_spells=True,
    ),
    "full_reset": RestoreConfig(keep_player_stats=False, keep_inventory=False, keep_spells=False),
}
