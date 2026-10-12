"""Builds context for LLM prompts within token budgets."""
from __future__ import annotations

from typing import Any

from text_rpg.llm.token_budget import TokenBudget
from text_rpg.mechanics import world_clock


class ContextPacker:
    def __init__(self, token_budget: TokenBudget | None = None):
        self.budget = token_budget or TokenBudget()

    def pack_narrative_context(
        self,
        character: dict,
        location: dict,
        recent_events: list[dict],
        rag_context: dict[str, list[str]] | None = None,
        combat_state: dict | None = None,
        nearby_entities: list[dict] | None = None,
        world_time: int | None = None,
        narrator_hints: list[str] | None = None,
    ) -> str:
        sections: list[str] = []

        # Time of day context
        if world_time is not None:
            time_str = world_clock.format_short(world_time)
            sections.append(f"## Time\n{time_str}")

        sections.append(f"## Current Character\n{self._format_character(character)}")
        sections.append(f"## Current Location\n{self._format_location(location)}")

        if nearby_entities:
            entity_text = "\n".join(
                f"- {e['name']}: {e.get('description', 'No description')}"
                for e in nearby_entities[:5]
            )
            sections.append(f"## Nearby\n{entity_text}")

        if combat_state and combat_state.get("is_active"):
            sections.append(f"## Combat\n{self._format_combat(combat_state)}")

        if recent_events:
            events_text = "\n".join(f"- {e.get('description', '')}" for e in reversed(recent_events[:5]))
            sections.append(f"## Recent Events\n{events_text}")

        if rag_context:
            if rag_context.get("relevant_lore"):
                lore_text = "\n".join(f"- {l}" for l in rag_context["relevant_lore"][:3])
                sections.append(f"## World Lore\n{lore_text}")
            if rag_context.get("past_events"):
                past_text = "\n".join(f"- {e}" for e in rag_context["past_events"][:3])
                sections.append(f"## Relevant History\n{past_text}")
            if rag_context.get("srd_reference"):
                srd_text = "\n".join(f"- {s}" for s in rag_context["srd_reference"][:2])
                sections.append(f"## Reference Flavor (background color only)\n{srd_text}")

        if narrator_hints:
            hints_text = "\n".join(f"- {h}" for h in narrator_hints[:3])
            sections.append(f"## Ambient Details (weave in naturally if relevant)\n{hints_text}")

        return self.budget.trim_to_budget("\n\n".join(sections))

    def pack_action_context(
        self, raw_input: str, character: dict, location: dict, available_actions: list[str]
    ) -> str:
        actions_list = ", ".join(available_actions)
        return (
            f'Player input: "{raw_input}"\n'
            f"Character: {character.get('name', '?')} (Level {character.get('level', 1)} "
            f"{character.get('race', 'Unknown')} {character.get('char_class', 'Unknown')})\n"
            f"Location: {location.get('name', '?')} - {location.get('description', '')[:100]}\n"
            f"Available action types: {actions_list}"
        )

    def _format_character(self, char: dict) -> str:
        hp = char.get("hp_current", "?")
        hp_max = char.get("hp_max", "?")
        conditions = ", ".join(char.get("conditions", [])) or "None"
        return (
            f"{char.get('name', '?')} - Level {char.get('level', 1)} "
            f"{char.get('race', 'Unknown')} {char.get('char_class', 'Unknown')}\n"
            f"HP: {hp}/{hp_max} | AC: {char.get('ac', 10)} | Conditions: {conditions}"
        )

    def _format_location(self, loc: dict) -> str:
        exits = []
        for conn in loc.get("connections", []):
            if isinstance(conn, dict):
                exits.append(f"{conn.get('direction', '?')} -> {conn.get('description', conn.get('target_location_id', '?'))}")
        exits_text = ", ".join(exits) if exits else "None visible"
        return f"{loc.get('name', '?')}: {loc.get('description', '')}\nExits: {exits_text}"

    def _format_combat(self, combat: dict) -> str:
        lines = [f"Round {combat.get('round_number', 1)}"]
        for c in combat.get("combatants", []):
            status = "ACTIVE" if c.get("is_active") else "waiting"
            hp_info = ""
            if isinstance(c.get("hp"), dict):
                hp_info = f" HP:{c['hp'].get('current', '?')}/{c['hp'].get('max', '?')}"
            lines.append(f"  {c.get('name', '?')} [{status}]{hp_info}")
        return "\n".join(lines)
