"""Compile every location into a stable, interactive scene.

The compiler is deterministic: prose may suggest a feature, but the engine
owns its operations and consequences. SceneActions persists the resulting
objects, so searches and physical changes survive reloads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from text_rpg.utils import safe_json


_DURABLE_ACTION = re.compile(
    r"^\s*(?:i\s+)?(?:(?:try|attempt|want)\s+to\s+|will\s+)?"
    r"(dig|build|construct|assemble|arrange|stack|pile|drag|clean|clear|sweep|"
    r"decorate|plant|tie|secure|block|unblock|bury|fill|redirect|divert|dam|paint)\b",
    re.I,
)
_NON_PHYSICAL_CLAIMS = re.compile(
    r"\b(cast|summon|conjure|wish|teleport|resurrect|revive|kill|attack|steal|"
    r"create\s+(?:gold|money|treasure)|become\s+(?:a\s+)?god)\b",
    re.I,
)


def durable_scene_change(action_text: str, actor_name: str, action_id: str,
                         world_time: int, entity_names: list[str] | None = None,
                         scene_text: str = "") -> dict | None:
    """Return a grounded persistent alteration for an eligible physical action.

    The record uses the player's attempted work rather than model-authored
    facts. Rewards, creatures, routes, magic, and changes to people remain
    under their dedicated systems.
    """
    clean = " ".join((action_text or "").split()).strip(" .")[:180]
    if not clean or not _DURABLE_ACTION.search(clean) or _NON_PHYSICAL_CLAIMS.search(clean):
        return None
    lowered = clean.lower()
    if any(name and name.lower() in lowered for name in (entity_names or [])):
        return None
    verb = _DURABLE_ACTION.search(clean).group(1).lower()
    if verb in {"redirect", "divert", "dam"}:
        described = scene_text.lower()
        requested = next((word for word in ("stream", "river", "waterfall", "canal", "water")
                          if re.search(rf"\b{word}\b", lowered)), None)
        if requested and not re.search(rf"\b{requested}\b", described):
            return None
    return {
        "id": f"change:{action_id}",
        "actor": actor_name or "The player",
        "description": clean,
        "world_time": int(world_time or 0),
        "state": "present",
    }


@dataclass(frozen=True)
class Feature:
    name: str
    description: str
    search_result: str
    operations: tuple[str, ...] = ("inspect", "search", "touch", "listen", "smell")
    flags: tuple[str, ...] = ()


_TYPE_FEATURES: dict[str, tuple[Feature, ...]] = {
    "settlement": (
        Feature("public space", "A shared patch of ground bears the scuffs and traces of daily traffic.", "The traffic marks show where residents gather and which route is used most often.", flags=("sittable",)),
        Feature("building fronts", "Doors, shutters, signs, and patched walls show how people here live and work.", "Small repairs and fresh wear distinguish occupied buildings from quiet ones.", operations=("inspect", "search", "touch", "listen", "smell", "read")),
        Feature("street edge", "Drains, stacked materials, and narrow gaps collect the overlooked debris of ordinary life.", "You find signs of recent passage, but no unclaimed valuables.", flags=("cover",)),
    ),
    "town": (
        Feature("town square", "A central thoroughfare ties the surrounding businesses and homes together.", "Wheel marks and footprints reveal the busiest route through town.", flags=("sittable",)),
        Feature("notice board", "Weathered notices and local announcements overlap on a public board.", "The newest notices concern current local work; established campaign leads remain the reliable objectives.", operations=("inspect", "search", "touch", "read")),
        Feature("side street", "A narrower lane runs between the main buildings, with doorways and service entrances along it.", "The lane shows ordinary deliveries and foot traffic, with no secret route evident.", flags=("cover",)),
    ),
    "wilderness": (
        Feature("terrain", "Uneven natural ground records weather, animals, and recent passage.", "You distinguish old natural marks from the freshest tracks, but find no guaranteed trail to treasure.", operations=("inspect", "search", "touch", "listen", "smell", "track")),
        Feature("vegetation", "Local plants provide broken sight lines and pockets of cover.", "Bent stems and disturbed leaves show where something passed recently.", flags=("cover", "climbable", "destructible")),
        Feature("distant landmark", "A visible rise, tree line, or rock formation helps fix your position.", "From the clearest angle you can compare the landmark with the known exits.", operations=("inspect", "search", "listen")),
    ),
    "road": (
        Feature("road surface", "Ruts, footprints, loose stones, and repaired patches mark the travelled way.", "The newest marks indicate recent traffic along the road, but not who made it.", operations=("inspect", "search", "touch", "track")),
        Feature("roadside cover", "Brush, stones, or a shallow verge border the route.", "The cover could conceal a crouching creature, though it hides no cache now.", flags=("cover", "sittable")),
        Feature("way marker", "A landmark beside the route helps travellers judge direction and distance.", "Wear on the side facing the road confirms the route most travellers take.", operations=("inspect", "search", "touch", "read"), flags=("destructible", "markable")),
    ),
    "dungeon": (
        Feature("walls and supports", "The surrounding stonework and supports carry stains, tool marks, and signs of age.", "A close check reveals stress and repairs, but no safe hidden passage.", operations=("inspect", "search", "touch", "listen")),
        Feature("floor", "Dust, grit, moisture, and traffic marks cover the floor.", "The freshest disturbance shows recent movement through the area.", operations=("inspect", "search", "touch", "track")),
        Feature("shadowed recess", "A dark recess breaks the outline of the chamber.", "The recess offers cover, but contains no unearned treasure.", operations=("inspect", "search", "listen", "smell"), flags=("cover",)),
    ),
    "shop": (
        Feature("display", "Goods and examples of the proprietor's work are arranged for inspection.", "Price marks and handling wear identify the popular stock; purchases still go through the shopkeeper.", operations=("inspect", "search", "touch", "read")),
        Feature("counter", "A worked counter separates customers from private stock and records.", "The public side holds nothing free to take.", flags=("sittable",)),
        Feature("work area", "Tools, packing material, and current work show the rhythm of the business.", "The arrangement reveals the trade being practiced without granting access to private supplies.", operations=("inspect", "search", "listen", "smell")),
    ),
    "temple": (
        Feature("altar", "An altar or sacred focus anchors the chamber.", "Offerings and wear show how the site is used; none are abandoned loot.", operations=("inspect", "search", "touch", "kneel")),
        Feature("sacred symbols", "Carved, painted, or hung symbols express the faith practiced here.", "Repeated motifs identify the values emphasized by the local worshippers.", operations=("inspect", "search", "touch", "read")),
        Feature("votive lights", "Small flames or reflected light soften the sacred space.", "The pattern of recently tended lights shows that the sanctuary is maintained.", operations=("inspect", "search", "touch", "smell")),
    ),
    "tavern": (
        Feature("common tables", "Scarred tables and benches hold the traces of meals, games, and conversation.", "Scratches and stains tell ordinary stories, but reveal no forgotten purse.", flags=("sittable", "destructible", "markable")),
        Feature("hearth", "A working hearth gives the room warmth, smoke, and a place for cooking.", "Ash and fresh fuel show when it was last tended.", operations=("inspect", "search", "touch", "listen", "smell")),
        Feature("rumour board", "Notes, marks, and scraps near the bar collect local concerns.", "Current formal objectives remain in leads; the scraps add local colour rather than a new reward.", operations=("inspect", "search", "touch", "read")),
    ),
    "ruins": (
        Feature("fallen masonry", "Broken stone and collapsed material divide the surviving structure.", "The rubble shows the direction of the collapse and a stable place to cross.", flags=("climbable", "cover")),
        Feature("weathered carving", "A surviving carved surface has been softened by damage and weather.", "Protected grooves preserve part of the original design.", operations=("inspect", "search", "touch", "read")),
        Feature("concealed gap", "A narrow gap runs behind the fallen material.", "The gap provides cover but closes after a short distance.", flags=("cover",)),
    ),
    "residence": (
        Feature("furnishings", "Everyday furniture shows how the occupant uses the room.", "Wear and placement reveal habits, not permission to take belongings.", flags=("sittable",)),
        Feature("window", "The window frames a view outside and admits changing light and sound.", "The sill records ordinary dust and recent opening.", operations=("inspect", "search", "touch", "listen")),
        Feature("storage", "Closed personal storage stands among the room's furnishings.", "It belongs to the resident; searching it without permission would be theft.", operations=("inspect", "listen")),
    ),
}


_KEYWORD_FEATURES: tuple[tuple[tuple[str, ...], Feature], ...] = (
    (("tree", "forest", "woodland"), Feature("nearby tree", "A mature tree offers bark, roots, branches, and cover from the main approach.", "Behind the trunk, roots hold leaves and signs of small animals, but no hidden cache.", flags=("cover", "climbable", "sittable", "destructible", "markable"))),
    (("river", "stream", "waterfall", "shore", "water"), Feature("water's edge", "Moving or standing water changes the sound, footing, and visibility nearby.", "The bank shows recent water level and tracks where creatures approached.", operations=("inspect", "search", "touch", "listen", "smell", "wade"))),
    (("crate", "cargo", "barrel"), Feature("stacked cargo", "Containers are stacked or secured nearby, each marked by use and ownership.", "The accessible markings identify ordinary shipments; sealed cargo is not free property.", operations=("inspect", "search", "touch", "read", "listen"), flags=("destructible", "markable"))),
    (("dock", "wharf", "pier", "boat", "ship"), Feature("waterside structures", "Ropes, pilings, planks, and working vessels connect the shore to the water.", "Wet marks and taut lines show which berths are currently in use.", operations=("inspect", "search", "touch", "listen", "smell", "climb"), flags=("climbable",))),
    (("forge", "anvil", "smith", "hammer"), Feature("forge works", "Heat, tools, fuel, and worked metal define an active craft area.", "Tool placement and scale reveal current work without making the materials available to take.", operations=("inspect", "search", "touch", "listen", "smell"))),
    (("statue", "monument"), Feature("monument", "A worked figure or monument presents a deliberate public image.", "Weathering and offerings reveal which details visitors touch most often.", operations=("inspect", "search", "touch", "read"), flags=("climbable", "markable"))),
    (("gate", "door", "portcullis"), Feature("main entrance", "A constructed entrance controls movement and bears signs of security and use.", "Hinges, tracks, and wear show how it opens; normal travel still uses the listed exit.", operations=("inspect", "search", "touch", "listen", "knock"), flags=("destructible", "markable"))),
    (("market", "stall", "merchant"), Feature("market stalls", "Trading spaces display wares, signs, and the evidence of bargaining.", "Labels and empty spaces show what sells quickly; goods still require a willing seller.", operations=("inspect", "search", "touch", "listen", "smell", "read"))),
    (("bridge",), Feature("bridge structure", "The crossing exposes its supports, rails, and surface to inspection.", "Wear and vibration reveal the soundest line across.", operations=("inspect", "search", "touch", "listen", "climb"), flags=("climbable",))),
    (("cave", "tunnel", "mine", "passage"), Feature("rock passage", "Natural stone and worked sections carry echoes, mineral stains, and tool marks.", "Airflow and scuffs distinguish the most recently used direction.", operations=("inspect", "search", "touch", "listen", "smell", "track"))),
    (("fire", "flame", "lava", "volcan"), Feature("source of heat", "Heat alters the air and makes nearby surfaces hazardous to touch.", "Ash and colour show where the heat is strongest; approaching carelessly would be dangerous.", operations=("inspect", "search", "listen", "smell"))),
)


_BESPOKE: dict[str, tuple[Feature, ...]] = {
    "lava_tubes": (
        Feature("glassy wall", "Ripples in the black glass catch orange light above a cooler ledge.", "Soot is rubbed away at hand height, marking a safer line through the corridor.", flags=("climbable", "sittable")),
        Feature("floor cracks", "Narrow seams glow orange and radiate punishing heat.", "The brightest seams lie away from the north wall; that side offers safer footing.", operations=("inspect", "search", "listen", "smell")),
    ),
    "old_mine_shaft": (
        Feature("cave-in", "Broken timbers protrude from load-bearing rubble while deliberate tapping carries through it.", "A narrow air gap carries the tapping, but safe excavation would require proper supports.", operations=("inspect", "search", "touch", "listen")),
        Feature("warning signs", "Dust cakes closure signs beside the blocked workings.", "The warning identifies unstable supports. Your search also uncovers one abandoned iron ingot behind a fallen sign.", operations=("inspect", "search", "touch", "read")),
    ),
    "deep_mines": (
        Feature("timber supports", "Iron braces bite into soot-darkened beams; fine grit lies in the joints.", "Fresh grit beneath one brace suggests movement worth reporting to Mine Boss Gretta.", operations=("inspect", "search", "touch", "listen")),
        Feature("lanterns", "Small flames mark the main shaft while side passages swallow their light.", "The brackets face the main route and can keep you oriented toward the forge.", operations=("inspect", "search", "touch", "smell")),
    ),
}


_DESCRIPTION_NOUNS: dict[str, tuple[str, ...]] = {
    "tree": ("climbable", "cover", "sittable", "destructible", "markable"), "rock": ("climbable", "cover", "markable"),
    "wall": ("markable",), "door": ("destructible", "markable"), "gate": ("destructible", "markable"), "road": (), "path": (), "trail": (),
    "crate": ("destructible", "markable"), "barrel": ("destructible", "markable"), "table": ("sittable", "destructible", "markable"), "chair": ("sittable", "destructible"),
    "bench": ("sittable", "destructible", "markable"), "desk": ("destructible", "markable"), "shelf": ("destructible",), "altar": ("markable",), "statue": ("climbable", "markable"),
    "fountain": (), "well": (), "river": (), "stream": (), "pool": (), "lake": (),
    "waterfall": (), "cliff": ("climbable",), "ledge": ("climbable", "sittable"),
    "bridge": ("climbable",), "tower": ("climbable",), "building": (), "house": (),
    "hut": (), "tent": (), "wagon": (), "cart": (), "ship": (), "boat": (), "dock": (),
    "pier": (), "tunnel": (), "passage": (), "cave": (), "rubble": ("cover",),
    "fire": (), "hearth": (), "forge": (), "anvil": ("markable",), "window": ("destructible", "markable"), "sign": ("destructible", "markable"),
    "board": ("destructible", "markable"), "stall": ("destructible", "markable"), "counter": ("destructible", "markable"), "floor": ("markable",), "ceiling": (), "root": (),
    "bush": ("cover", "destructible"), "flower": ("destructible",), "track": (), "cage": ("destructible",), "chain": (), "rope": ("destructible",),
    "ladder": ("climbable",), "stair": (), "step": (), "arch": (), "pillar": ("cover",),
    "column": ("cover",), "throne": ("sittable",), "bed": ("sittable",), "tapestry": (),
    "painting": (), "book": (), "scroll": (), "brazier": (), "lantern": (),
}


def _description_features(description: str) -> list[Feature]:
    """Expose concrete nouns already present in prose as targetable objects."""
    features: list[Feature] = []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", description) if part.strip()]
    for noun, flags in _DESCRIPTION_NOUNS.items():
        sentence = next((part for part in sentences if re.search(rf"\b{re.escape(noun)}(?:s|es)?\b", part, re.I)), None)
        if not sentence:
            continue
        operations = ["inspect", "search", "touch", "listen", "smell"]
        if noun in {"sign", "board", "book", "scroll", "painting", "tapestry", "altar", "statue"}:
            operations.append("read")
        if noun in {"river", "stream", "pool", "lake", "waterfall"}:
            operations.append("wade")
        if noun in {"door", "gate"}:
            operations.append("knock")
        features.append(Feature(
            noun,
            sentence,
            f"You carefully check around and behind the {noun}. Its condition matches the surrounding scene; you find no unearned treasure.",
            operations=tuple(operations), flags=flags,
        ))
        if len(features) == 3:
            break
    return features


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def details(location: dict) -> list[dict]:
    """Return at least three deterministic affordances for a viable location."""
    props = safe_json(location.get("properties"), {})
    description = location.get("description") or ""
    if props.get("condition") == "destroyed" or description.startswith("This place was destroyed."):
        return []

    features: list[Feature] = list(_BESPOKE.get(location.get("id"), ()))
    features.extend(_description_features(description))
    lower = description.lower()
    for words, feature in _KEYWORD_FEATURES:
        existing_names = {candidate.name.lower() for candidate in features}
        if any(word in lower for word in words) and not any(word in existing_names for word in words):
            features.append(feature)
    kind = location.get("location_type") or "wilderness"
    features.extend(_TYPE_FEATURES.get(kind, _TYPE_FEATURES["wilderness"]))

    unique: list[Feature] = []
    seen: set[str] = set()
    for feature in features:
        key = _slug(feature.name)
        if key not in seen:
            seen.add(key)
            unique.append(feature)
        if len(unique) == 6:
            break

    location_id = location.get("id") or "unknown"
    objects = []
    for feature in unique:
        operations = list(dict.fromkeys(feature.operations))
        if "climbable" in feature.flags and "climb" not in operations:
            operations.append("climb")
        if "sittable" in feature.flags and "sit" not in operations:
            operations.append("sit")
        if "cover" in feature.flags and "hide_behind" not in operations:
            operations.append("hide_behind")
        if "destructible" in feature.flags and "damage" not in operations:
            operations.append("damage")
            operations.append("repair")
        if "markable" in feature.flags and "mark" not in operations:
            operations.append("mark")
            operations.append("clean")
        state = {
            "operations": operations,
            "description": feature.description,
            "search_result": feature.search_result,
            "search_dc": 10,
            "flags": list(feature.flags),
        }
        if "destructible" in feature.flags:
            state["hp"] = 6
            state["hp_max"] = 6
        objects.append({"id": f"detail:{location_id}:{_slug(feature.name)}", "name": feature.name, "state": state})

    if location_id == "old_mine_shaft":
        warning = next(obj for obj in objects if obj["name"] == "warning signs")
        warning["state"]["search_items"] = ["iron_ingot"]
    return objects


def danger_encounters(location: dict) -> list[dict]:
    from text_rpg.content.loader import load_region_encounters
    if not location.get("region_id"):
        return []
    try:
        return [event for event in load_region_encounters(location["region_id"])
                if event.get("entities") and location.get("id") in event.get("location_ids", [])]
    except (FileNotFoundError, KeyError, ValueError):
        return []


def danger_rating(location: dict, player_level: int) -> dict | None:
    """Summarize authored combat danger relative to the current character."""
    encounters = danger_encounters(location)
    templates = [template for encounter in encounters for template in encounter.get("entities", [])]
    if not templates:
        return None
    from text_rpg.mechanics.combat_math import assess_threat_level
    levels = [int(template.get("level", 1)) for template in templates]
    # Extra bodies matter even when each creature is individually equal-level.
    effective = max(int(template.get("level", 1)) + max(0, int(template.get("count_min", 1)) - 1)
                    for template in templates)
    return {
        "minimum_level": min(levels),
        "maximum_level": max(levels),
        "threat": assess_threat_level(int(player_level or 1), effective),
        "names": sorted({template.get("name", "hostile creature") for template in templates}),
    }
_AMBIENT = {
    "settlement": "Residents cross between homes and work while nearby conversations overlap.",
    "town": "Workers, residents, and travellers pass through on their own errands.",
    "road": "Wind and distant travel sounds periodically break the quiet of the road.",
    "wilderness": "Wind, insects, birds, and shifting vegetation make the landscape sound inhabited.",
    "dungeon": "Air moves through unseen gaps while small echoes make distance hard to judge.",
    "shop": "Handling sounds, quiet calculation, and the smell of stock give the business its rhythm.",
    "temple": "Low voices, careful footsteps, and ritual scents shape the sanctuary's quiet.",
    "tavern": "Conversation, crockery, footsteps, and the hearth overlap in a steady room-wide murmur.",
    "ruins": "Wind and small animals move through gaps left by the ruined structure.",
    "residence": "Small domestic sounds and settled belongings make the room feel occupied.",
}

_PERIOD_TONE = {
    "dawn": " Dawn light and the first movements of the day are changing the scene.",
    "morning": " Morning activity is well underway.",
    "midday": " Midday light exposes details that shadows usually soften.",
    "afternoon": " The day's accumulated tracks, work, and wear are becoming visible.",
    "evening": " Evening light lengthens shadows as daytime work begins to slow.",
    "night": " Darkness narrows sight lines while nearby sounds carry farther.",
    "late_night": " Most ordinary activity has stopped, leaving isolated lights and quieter movement.",
}


def scene_summary(location: dict, objects: list[dict], entities: list[dict] | None = None,
                  period: str | None = None, game_id: str = "", world_time: int = 0) -> str:
    """Concise, actionable scene text for both initial display and `look`."""
    lines: list[str] = []
    if game_id and location.get("region_id"):
        from text_rpg.content.loader import load_all_regions
        from text_rpg.mechanics.weather import current_weather
        climate = load_all_regions().get(location["region_id"], {}).get("climate", "temperate")
        weather = current_weather(game_id, location["region_id"], climate, world_time)
        warning = " Travel and exposed physical checks are harder." if weather["severe"] else ""
        lines.append(f"Weather outside: {weather['name']}.{warning}")
    if danger_encounters(location):
        lines.append("Danger: hostile encounters are possible here. Use `hunt` to seek them deliberately; `look` is safe.")
    props = safe_json(location.get("properties"), {})
    active_puzzle = props.get("active_puzzle")
    if active_puzzle:
        puzzle_name = active_puzzle.get("name", "Unresolved obstacle")
        puzzle_type = active_puzzle.get("puzzle_type", "puzzle").replace("_", " ")
        lines.append(f"Obstacle: {puzzle_name} ({puzzle_type}) remains unresolved. "
                     "Try `solve <approach>`, `answer <response>`, `disarm <method>`, or `pick lock`.")
    if not (location.get("description") or "").startswith("This place was destroyed."):
        ambient = _AMBIENT.get(location.get("location_type") or "wilderness", _AMBIENT["wilderness"])
        lines.append("Activity: " + ambient + _PERIOD_TONE.get(period or "", ""))
        if entities and period:
            from text_rpg.mechanics.world_sim import get_ambient_activity
            activities = get_ambient_activity(location.get("id", ""), entities, period)
            if activities:
                lines.append("Nearby: " + " ".join(activities[:3]))
    if objects:
        feature_lines = []
        for obj in objects:
            state = obj["state"]
            statuses = []
            if state.get("searched"):
                statuses.append("searched")
            elif state.get("search_attempts"):
                statuses.append("search attempted")
            if state.get("hp_max") and state.get("hp", state["hp_max"]) < state["hp_max"]:
                statuses.append("broken" if state.get("hp") == 0 else "damaged")
            if state.get("marks"):
                statuses.append("marked")
            suffix = " — " + ", ".join(statuses) if statuses else ""
            feature_lines.append(f"- {obj['name']}{suffix}: {state.get('description', '')}")
        lines.append("Visible features:\n" + "\n".join(feature_lines))
        verbs = sorted({op.replace("_", " ") for obj in objects for op in obj["state"].get("operations", [])})
        extra = [verb for verb in verbs if verb not in {"inspect", "search"}]
        prompt = "Try: `inspect <feature>` or `search behind <feature>`"
        if extra:
            prompt += "; this scene also supports " + ", ".join(f"`{verb} <feature>`" for verb in extra)
        lines.append(prompt + ".")
    traces = props.get("player_traces", [])
    if any(trace.get("kind") == "relieve_self" for trace in traces):
        lines.append("A mess left on the ground marks your earlier visit.")
    changes = props.get("scene_changes", [])
    present_changes = [change for change in changes if change.get("state", "present") == "present"]
    if present_changes:
        lines.append("Lasting changes:\n" + "\n".join(
            f"- {change.get('actor', 'Someone')}: {change.get('description', 'altered the area')}"
            for change in present_changes[-8:]
        ))
    battles = props.get("battle_history", [])
    if battles:
        latest = battles[-1]
        opponents = latest.get("opponents") or latest.get("defeated") or ["unknown foes"]
        lines.append("Battle aftermath: signs of a recent fight remain here after the clash with "
                     + ", ".join(opponents) + ".")
    return "\n\n".join(lines)
