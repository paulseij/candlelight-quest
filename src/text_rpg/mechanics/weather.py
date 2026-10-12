"""Deterministic regional weather with small exploration consequences."""
from __future__ import annotations

import hashlib


_PATTERNS = {
    "maritime": ("clear sea breeze", "drizzle", "sea fog", "rain squall"),
    "storm": ("hard rain", "gale winds", "thunderstorm", "brief stormbreak"),
    "cold": ("clear cold", "light snow", "icy wind", "whiteout"),
    "arid": ("dry clear sky", "dust wind", "cold dry air", "sandstorm"),
    "swamp": ("humid still air", "low mist", "steady rain", "thick fog"),
    "volcanic": ("sulfur haze", "hot wind", "light ashfall", "heavy ashfall"),
    "planar": ("shimmering calm", "arcane wind", "luminous haze", "reality storm"),
    "temperate": ("clear sky", "overcast", "light rain", "strong wind"),
}


def _family(climate: str) -> str:
    climate = (climate or "temperate").lower()
    if "storm" in climate:
        return "storm"
    if any(word in climate for word in ("arctic", "alpine", "mountain_cold")):
        return "cold"
    if any(word in climate for word in ("arid", "wasteland", "plains")) and "volcan" not in climate:
        return "arid"
    if any(word in climate for word in ("swamp", "mist")):
        return "swamp"
    if "volcan" in climate:
        return "volcanic"
    if "planar" in climate:
        return "planar"
    if "maritime" in climate:
        return "maritime"
    return "temperate"


def current_weather(game_id: str, region_id: str, climate: str, world_time: int) -> dict:
    """Return stable weather for a six-hour world-time block."""
    family = _family(climate)
    block = int(world_time or 0) // 360
    digest = hashlib.sha256(f"{game_id}\0{region_id}\0{block}".encode()).digest()
    name = _PATTERNS[family][digest[0] % len(_PATTERNS[family])]
    severe = name in {"rain squall", "gale winds", "thunderstorm", "whiteout",
                      "sandstorm", "thick fog", "heavy ashfall", "reality storm"}
    wet = name in {"drizzle", "rain squall", "hard rain", "thunderstorm", "light rain", "steady rain"}
    obscured = severe or name in {"sea fog", "low mist", "luminous haze", "light ashfall"}
    return {
        "name": name,
        "severe": severe,
        "climb_dc": 4 if severe else 2 if wet else 0,
        "track_dc": 2 if severe or obscured else -1 if wet else 0,
        "search_dc": 2 if obscured else 0,
    }

