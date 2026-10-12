"""Pure functions for NPC scheduling and ambient world activity."""
from __future__ import annotations

import json
from typing import Any

from text_rpg.utils import safe_json

# Activity descriptions keyed by profession and time period.
NPC_ACTIVITIES: dict[str, dict[str, str]] = {
    "resident": {
        "dawn": "opening shutters and beginning the day's chores",
        "morning": "carrying supplies between nearby homes and workplaces",
        "midday": "pausing to exchange local news with passersby",
        "afternoon": "repairing an everyday tool near the public way",
        "evening": "finishing errands before the light fades",
        "night": "heading home beneath the remaining lights",
        "late_night": "sleeping at home",
    },
    "customer": {
        "dawn": "waiting for the business to open",
        "morning": "comparing goods and asking about prices",
        "midday": "collecting a wrapped purchase from the counter",
        "afternoon": "examining the remaining stock",
        "evening": "finishing a last purchase before closing",
        "night": "walking home with their purchases",
        "late_night": "sleeping elsewhere",
    },
    "patron": {
        "dawn": "finishing a quiet breakfast near the hearth",
        "morning": "sharing road news over a modest meal",
        "midday": "eating while listening to nearby conversation",
        "afternoon": "playing a small table game with another patron",
        "evening": "talking above the crowded common-room noise",
        "night": "nursing a final drink near the fire",
        "late_night": "sleeping elsewhere",
    },
    "worshipper": {
        "dawn": "taking part in the first quiet observance",
        "morning": "leaving a modest offering and speaking softly",
        "midday": "sitting in reflection near the sacred focus",
        "afternoon": "helping straighten the public sanctuary",
        "evening": "lighting a votive and preparing to leave",
        "night": "keeping a silent vigil",
        "late_night": "sleeping elsewhere",
    },
    "innkeeper": {
        "dawn": "preparing the morning fire and breakfast",
        "morning": "serving porridge and ale to early risers",
        "midday": "out buying supplies from local farmers",
        "afternoon": "chopping vegetables and roasting meat for the evening",
        "evening": "bustling behind the bar during the busy hours",
        "night": "wiping down tables and counting the day's coins",
        "late_night": "sleeping upstairs above the tavern",
    },
    "blacksmith": {
        "dawn": "stoking the forge and heating the first ingots",
        "morning": "hammering steel at the anvil, sparks flying",
        "midday": "taking a break, sharing a meal near the forge",
        "afternoon": "tempering blades and fitting handles",
        "evening": "banking the forge coals and tidying the shop",
        "night": "examining a commission by lantern light",
        "late_night": "sleeping in the back room",
    },
    "guard": {
        "dawn": "starting the morning patrol, checking the perimeter",
        "morning": "standing watch at the village entrance",
        "midday": "rotating shifts with fellow guards",
        "afternoon": "patrolling the outskirts and nearby roads",
        "evening": "lighting torches along the main roads",
        "night": "standing night watch, alert for trouble",
        "late_night": "dozing at the guard post between rounds",
    },
    "healer": {
        "dawn": "gathering fresh herbs from the garden",
        "morning": "tending to patients and preparing remedies",
        "midday": "offering prayers and blessings at the shrine",
        "afternoon": "mixing poultices and checking on the sick",
        "evening": "meditating quietly in the sanctuary",
        "night": "reading ancient texts by candlelight",
        "late_night": "resting in the temple quarters",
    },
    "farmer": {
        "dawn": "heading out to the fields with tools over one shoulder",
        "morning": "working the soil, tending crops row by row",
        "midday": "resting under a tree, eating bread and cheese",
        "afternoon": "hauling water from the well to the fields",
        "evening": "returning home, tired but satisfied",
        "night": "mending tools by the hearth",
        "late_night": "sleeping soundly after a long day",
    },
    "merchant": {
        "dawn": "unlocking the shop and arranging displays",
        "morning": "greeting customers and haggling over prices",
        "midday": "restocking shelves from the back storeroom",
        "afternoon": "tallying accounts and writing orders",
        "evening": "closing up shop and counting profits",
        "night": "reviewing ledgers at home",
        "late_night": "sleeping above the shop",
    },
    "priest": {
        "dawn": "leading the dawn prayers for early worshippers",
        "morning": "counseling villagers who seek guidance",
        "midday": "leading a midday service",
        "afternoon": "visiting the sick and offering comfort",
        "evening": "lighting votive candles and tending the altar",
        "night": "studying sacred texts",
        "late_night": "in deep meditation",
    },
}

_DEFAULT_ACTIVITIES: dict[str, str] = {
    "dawn": "beginning their daily routine",
    "morning": "going about their morning tasks",
    "midday": "taking a midday break",
    "afternoon": "busy with afternoon work",
    "evening": "winding down for the evening",
    "night": "settling in for the night",
    "late_night": "sleeping",
}


def _parse_schedule(npc: dict) -> dict[str, str] | None:
    """Parse the schedule field from an NPC dict (may be JSON string or dict)."""
    schedule = npc.get("schedule")
    if schedule is None:
        return None
    parsed = safe_json(schedule, None)
    return parsed if isinstance(parsed, dict) else None


def _parse_unavailable(npc: dict) -> list[str]:
    """Parse unavailable_periods from NPC dict."""
    periods = safe_json(npc.get("unavailable_periods"), [])
    return periods if isinstance(periods, list) else []


def get_npc_location(npc: dict, period: str) -> str | None:
    """Return where the NPC should be during *period*, or None if unavailable."""
    if period in _parse_unavailable(npc):
        return None
    schedule = _parse_schedule(npc)
    if schedule:
        return schedule.get(period, npc.get("location_id"))
    return npc.get("location_id")


def is_npc_available(npc: dict, period: str) -> bool:
    """True if the NPC is available for interaction during *period*."""
    if not npc.get("is_alive", True):
        return False
    return period not in _parse_unavailable(npc)


def get_npc_activity(npc: dict, period: str) -> str:
    """Describe what the NPC is doing right now."""
    profession = (npc.get("profession") or "").lower()
    activities = NPC_ACTIVITIES.get(profession, _DEFAULT_ACTIVITIES)
    return activities.get(period, _DEFAULT_ACTIVITIES.get(period, "going about their business"))


def get_available_npcs(npcs: list[dict], period: str) -> list[dict]:
    """Filter to NPCs that are alive and available during *period*."""
    return [n for n in npcs if is_npc_available(n, period)]


def get_ambient_activity(location_id: str, npcs: list[dict], period: str) -> list[str]:
    """Return ambient activity strings for NPCs present at *location_id* during *period*."""
    hints: list[str] = []
    for npc in npcs:
        if not npc.get("is_alive", True):
            continue
        npc_loc = get_npc_location(npc, period)
        if npc_loc == location_id and is_npc_available(npc, period):
            activity = get_npc_activity(npc, period)
            name = npc.get("name", "Someone")
            hints.append(f"{name} is {activity}.")
    return hints
