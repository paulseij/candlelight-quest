"""Item invention mechanics — rarity gates, effect budgets, DCs, and mishaps.

Pure mechanics — no I/O. Mirrors spell_invention.py; used by the CraftingSystem
when players invent or enchant items.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Crafting location bonuses (negative = easier)
ITEM_LOCATION_BONUSES: dict[str, int] = {
    "forge": -4,
    "workshop": -3,
    "smithy": -4,
    "shop": -2,
    "academy": -2,
    "arcane_tower": -3,
}

# Rarity ladder: minimum character level and DC modifier
RARITY_REQUIREMENTS: dict[str, int] = {
    "common": 1,
    "uncommon": 3,
    "rare": 6,
}

RARITY_DC_MODIFIER: dict[str, int] = {
    "common": 0,
    "uncommon": 6,
    "rare": 14,
}

# Effect budget points per rarity
RARITY_BUDGETS: dict[str, int] = {
    "common": 2,
    "uncommon": 4,
    "rare": 6,
}

# Per-rarity caps for scaling effects
MAX_DAMAGE_DICE_BY_RARITY: dict[str, str] = {
    "common": "1d4",
    "uncommon": "1d6",
    "rare": "1d8",
}

MAX_AC_BONUS_BY_RARITY: dict[str, int] = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
}

MAX_HEAL_DICE_BY_RARITY: dict[str, str] = {
    "common": "2d4",
    "uncommon": "4d4",
    "rare": "6d4",
}

# Closed effect menu: effect type -> budget cost
ITEM_EFFECT_COSTS: dict[str, int] = {
    "damage_dice": 2,       # bonus damage dice on an equipped weapon
    "ac_bonus": 3,          # +N AC while equipped
    "skill_bonus": 2,       # +1 to a named skill while carried
    "resistance": 3,        # resistance to one damage type
    "consumable_heal": 1,   # single-use healing
    "light": 1,             # sheds light
    "utility_text": 0,      # descriptive utility, no mechanics
}

VALID_ITEM_TYPES = frozenset({
    "weapon", "armor", "shield", "consumable", "tool", "trinket", "misc",
})


@dataclass
class ItemMishap:
    """Result of a failed item invention/enchant attempt."""
    description: str
    damage_to_crafter: int
    materials_lost: bool
    cursed_quirk: str | None


def max_rarity_for_level(character_level: int, crafting_skill_level: int = 0) -> str:
    """Highest rarity a character can attempt. Crafting skill can substitute
    for character level (a level-3 skilled crafter can attempt uncommon)."""
    effective = max(character_level, crafting_skill_level)
    if effective >= RARITY_REQUIREMENTS["rare"]:
        return "rare"
    if effective >= RARITY_REQUIREMENTS["uncommon"]:
        return "uncommon"
    return "common"


def validate_item_proposal(
    proposal: dict,
    character_level: int,
    crafting_skill_level: int = 0,
) -> tuple[bool, str]:
    """Validate an LLM item proposal against rarity gates and effect budgets.

    Returns (is_valid, reason). The proposal's effects list is filtered in
    place: unknown effect types are removed, magnitudes clamped to rarity caps.
    """
    rarity = str(proposal.get("rarity", "common")).lower()
    if rarity not in RARITY_REQUIREMENTS:
        proposal["rarity"] = rarity = "common"

    allowed = max_rarity_for_level(character_level, crafting_skill_level)
    if RARITY_REQUIREMENTS[rarity] > RARITY_REQUIREMENTS[allowed]:
        return False, (
            f"A {rarity} item is beyond your skill — you can attempt up to {allowed} "
            f"(level {RARITY_REQUIREMENTS[rarity]} or matching crafting skill required)."
        )

    item_type = str(proposal.get("item_type", "misc")).lower()
    if item_type not in VALID_ITEM_TYPES:
        proposal["item_type"] = "misc"

    budget = RARITY_BUDGETS[rarity]
    spent = 0
    kept: list[dict] = []
    for effect in proposal.get("effects", []):
        if not isinstance(effect, dict):
            continue
        etype = str(effect.get("type", "")).strip()
        params = effect.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        cost = ITEM_EFFECT_COSTS.get(etype)
        if cost is None:
            continue

        if etype == "damage_dice":
            dice = str(params.get("dice", "1d4"))
            if not _dice_within_limit(dice, MAX_DAMAGE_DICE_BY_RARITY[rarity]):
                params["dice"] = MAX_DAMAGE_DICE_BY_RARITY[rarity]
        elif etype == "ac_bonus":
            cap = MAX_AC_BONUS_BY_RARITY[rarity]
            if cap == 0:
                continue  # common items cannot grant AC
            params["amount"] = max(1, min(cap, _as_int(params.get("amount"), 1)))
        elif etype == "skill_bonus":
            params["amount"] = 1
            if not params.get("skill"):
                continue
        elif etype == "resistance":
            if not params.get("damage_type"):
                continue
        elif etype == "consumable_heal":
            dice = str(params.get("dice", "2d4"))
            if not _dice_within_limit(dice, MAX_HEAL_DICE_BY_RARITY[rarity]):
                params["dice"] = MAX_HEAL_DICE_BY_RARITY[rarity]

        if spent + cost > budget:
            continue
        spent += cost
        kept.append({"type": etype, "params": params})

    proposal["effects"] = kept
    return True, ""


def calculate_item_dc(
    plausibility: float,
    rarity: str,
    location_type: str | None,
    tool_proficient: bool,
) -> int:
    """DC for inventing an item, clamped to [5, 45]."""
    from text_rpg.systems.director.generators import plausibility_to_dc

    base_dc = plausibility_to_dc(plausibility)
    rarity_mod = RARITY_DC_MODIFIER.get(rarity, 0)
    location_bonus = ITEM_LOCATION_BONUSES.get(location_type or "", 0)
    tool_bonus = -2 if tool_proficient else 0

    dc = base_dc + rarity_mod + location_bonus + tool_bonus
    return max(5, min(45, dc))


def generate_crafting_mishap(rarity: str, margin_of_failure: int) -> ItemMishap:
    """Mishap from a failed invention, scaled by margin (like wild magic).

    - 1-5: materials wasted, nothing more
    - 6-10: minor injury, materials lost
    - 11+: injury and the piece survives — flawed, with a cursed quirk
    """
    if margin_of_failure <= 5:
        return ItemMishap(
            description=random.choice([
                "The piece cracks at the final step; the materials are ruined.",
                "Your hands slip and the work collapses into scrap.",
                "The design simply doesn't hold together. Wasted materials litter the bench.",
            ]),
            damage_to_crafter=0,
            materials_lost=True,
            cursed_quirk=None,
        )

    if margin_of_failure <= 10:
        damage = random.randint(1, 4)
        return ItemMishap(
            description=random.choice([
                f"A shard flies loose and cuts you for {damage} damage. The materials are ruined.",
                f"The forge spits sparks — {damage} damage — and the piece is beyond saving.",
            ]),
            damage_to_crafter=damage,
            materials_lost=True,
            cursed_quirk=None,
        )

    damage = random.randint(2, 8)
    quirk = random.choice([
        "It whispers faintly when no one is looking.",
        "It is always unnaturally cold to the touch.",
        "Its owner sleeps poorly while it is nearby.",
        "Small animals refuse to come near it.",
    ])
    severity = max(1, RARITY_REQUIREMENTS.get(rarity, 1) // 2)
    return ItemMishap(
        description=(
            f"The work twists strangely in the making, lashing out for {damage * severity} damage. "
            f"Something finished the piece — but not quite as you intended."
        ),
        damage_to_crafter=damage * severity,
        materials_lost=False,
        cursed_quirk=quirk,
    )


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dice_within_limit(dice_str: str, max_dice_str: str) -> bool:
    try:
        return _max_dice_value(dice_str) <= _max_dice_value(max_dice_str)
    except (ValueError, IndexError):
        return False


def _max_dice_value(dice_str: str) -> int:
    parts = dice_str.lower().split("+")
    dice_part = parts[0].strip()
    bonus = int(parts[1].strip()) if len(parts) > 1 else 0
    num, size = dice_part.split("d")
    return int(num) * int(size) + bonus
