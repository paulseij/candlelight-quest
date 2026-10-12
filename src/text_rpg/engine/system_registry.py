"""System registry — manages pluggable game systems."""
from __future__ import annotations

from typing import Any

from text_rpg.models.action import Action
from text_rpg.systems.base import GameContext, GameSystem


class SystemRegistry:
    def __init__(self) -> None:
        self._systems: dict[str, GameSystem] = {}

    def register(self, system: GameSystem) -> None:
        self._systems[system.system_id] = system

    def get_system(self, system_id: str) -> GameSystem | None:
        return self._systems.get(system_id)

    def find_system_for_action(self, action: Action, context: GameContext) -> GameSystem | None:
        for system in self._systems.values():
            if system.can_handle(action, context):
                return system
        return None

    def get_all_available_actions(self, context: GameContext) -> list[dict]:
        actions: list[dict] = []
        for system in self._systems.values():
            try:
                sys_actions = system.get_available_actions(context)
                if sys_actions:
                    actions.extend(sys_actions)
            except Exception:
                pass
        return actions

    def inject_all(self, **deps: Any) -> None:
        """Inject dependencies into all registered systems."""
        for system in self._systems.values():
            system.inject(**deps)

    def register_defaults(self) -> None:
        from text_rpg.systems.combat.system import CombatSystem
        from text_rpg.systems.companion.system import CompanionSystem
        from text_rpg.systems.housing.system import HousingSystem
        from text_rpg.systems.crafting.system import CraftingSystem
        from text_rpg.systems.exploration.system import ExplorationSystem
        from text_rpg.systems.inventory.system import InventorySystem
        from text_rpg.systems.rest.system import RestSystem
        from text_rpg.systems.shop.system import ShopSystem
        from text_rpg.systems.social.system import SocialSystem
        from text_rpg.systems.guild.system import GuildSystem
        from text_rpg.systems.spell_creation.system import SpellCreationSystem
        from text_rpg.systems.spellcasting.system import SpellcastingSystem
        from text_rpg.systems.revival.system import RevivalSystem

        self.register(CombatSystem())
        self.register(ExplorationSystem())
        self.register(SocialSystem())
        self.register(InventorySystem())
        self.register(RestSystem())
        self.register(CraftingSystem())
        self.register(SpellcastingSystem())
        self.register(RevivalSystem())
        self.register(SpellCreationSystem())
        self.register(ShopSystem())
        self.register(CompanionSystem())
        self.register(HousingSystem())
        self.register(GuildSystem())
