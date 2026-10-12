"""Shop system — handles buy, sell, browse actions."""
from __future__ import annotations

import json
from typing import Any

from text_rpg.content.loader import load_all_items
from text_rpg.mechanics.economy import calculate_buy_price, calculate_sell_price, supply_demand_modifier
from text_rpg.mechanics.reputation import get_effects
from text_rpg.models.action import Action, ActionResult, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json


class ShopSystem(GameSystem):
    """Handles buy, sell, and browse/shop actions."""

    def __init__(self) -> None:
        self._repos: dict[str, Any] | None = None

    def inject(self, *, repos: dict | None = None, **kwargs) -> None:
        if repos is not None:
            self._repos = repos

    @property
    def system_id(self) -> str:
        return "shop"

    @property
    def handled_action_types(self) -> set[str]:
        return {"buy", "sell", "browse"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() in self.handled_action_types

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        action_type = action.action_type.lower()
        if action_type == "buy":
            return self._resolve_buy(action, context)
        elif action_type == "sell":
            return self._resolve_sell(action, context)
        elif action_type == "browse":
            return self._resolve_browse(action, context)
        return ActionResult(action_id=action.id, success=False, outcome_description="Unknown shop action.")

    def get_available_actions(self, context: GameContext) -> list[dict]:
        shops = self._get_shops_at_location(context)
        if not shops:
            return []
        return [
            {"action_type": "browse", "description": "Browse a shop's wares"},
            {"action_type": "buy", "description": "Buy an item from a shop"},
            {"action_type": "sell", "description": "Sell an item to a shop"},
        ]

    def _get_shops_at_location(self, context: GameContext) -> list[dict]:
        """Get all shops at the player's current location."""
        repos = self._repos or {}
        shop_repo = repos.get("shop")
        if not shop_repo:
            return []
        shops = shop_repo.get_shop_by_location(context.game_id, context.location.get("id", ""))
        from text_rpg.storage.database import Database
        condition = None
        if isinstance(getattr(repos.get('save_game'), 'db', None), Database):
            from text_rpg.engine.continuity import Continuity
            condition = Continuity(repos).location_condition(context.game_id, context.location.get('id', ''))
        if condition and condition['condition'] == 'destroyed':
            return []
        entity_repo = repos.get('entity')
        if not entity_repo:
            return shops
        return [shop for shop in shops if not shop.get('owner_entity_id') or
                ((owner := entity_repo.get(shop['owner_entity_id'])) and owner.get('is_alive', False)
                 and owner.get('location_id') == shop.get('location_id'))]

    def _get_rep_multiplier(self, context: GameContext, shop: dict) -> float:
        """Get the reputation-based price multiplier for a shop."""
        repos = self._repos or {}
        rep_repo = repos.get("reputation")
        if not rep_repo:
            return 1.0

        # Find the faction associated with the shop owner
        owner_id = shop.get("owner_entity_id", "")
        entity_repo = repos.get("entity")
        if entity_repo and owner_id:
            entity = entity_repo.get(owner_id)
            if entity:
                faction_id = entity.get("faction_id")
                if faction_id:
                    rep = rep_repo.get_faction_rep(context.game_id, faction_id)
                    effects = get_effects(rep)
                    return effects.get("shop_price_mult", 1.0)
        return 1.0

    def _resolve_browse(self, action: Action, context: GameContext) -> ActionResult:
        """Show what shops are available and their stock."""
        shops = self._get_shops_at_location(context)
        if not shops:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="There are no shops here.",
            )

        all_items = load_all_items()
        parts: list[str] = []

        for shop in shops:
            # Find owner name
            owner_name = self._get_owner_name(shop, context)
            shop_type = shop.get("shop_type", "general").replace("_", " ").title()
            parts.append(f"**{owner_name}'s {shop_type} Shop**")

            rep_mult = self._get_rep_multiplier(context, shop)
            shop_price_mod = shop.get("price_modifier", 1.0)

            stock = shop.get("stock", [])
            if not stock:
                parts.append("  (No items in stock)")
                continue

            for entry in stock:
                item_id = entry.get("item_id", "")
                qty = entry.get("quantity", 0)
                base_price = entry.get("base_price", 0)
                if qty <= 0:
                    continue
                item_data = all_items.get(item_id, {})
                name = item_data.get("name", item_id.replace("_", " ").title())
                supply_mult = supply_demand_modifier(qty, entry.get("base_stock", qty))
                final_price = calculate_buy_price(base_price, rep_mult * shop_price_mod, supply_mult)
                parts.append(f"  {name} — {final_price} gp (x{qty})")

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description="\n".join(parts),
            events=[{
                "event_type": "SHOP_BROWSE",
                "description": "Browsed shop wares.",
                "actor_id": context.character.get("id", ""),
            }],
        )

    def _resolve_buy(self, action: Action, context: GameContext) -> ActionResult:
        """Buy an item from a shop."""
        item_name = (action.target_id or "").lower().strip()
        if not item_name:
            return ActionResult(action_id=action.id, success=False, outcome_description="Buy what? Type 'browse' to see what's for sale.")

        shops = self._get_shops_at_location(context)
        if not shops:
            return ActionResult(action_id=action.id, success=False, outcome_description="There are no shops here.")

        all_items = load_all_items()
        char = context.character
        player_gold = char.get("gold", 0)

        # Search all shops at this location for the requested item
        for shop in shops:
            stock = shop.get("stock", [])
            rep_mult = self._get_rep_multiplier(context, shop)
            shop_price_mod = shop.get("price_modifier", 1.0)

            for entry in stock:
                item_id = entry.get("item_id", "")
                qty = entry.get("quantity", 0)
                base_price = entry.get("base_price", 0)
                if qty <= 0:
                    continue

                item_data = all_items.get(item_id, {})
                display_name = item_data.get("name", item_id.replace("_", " ")).lower()

                if item_name in display_name or display_name in item_name or item_name in item_id.replace("_", " "):
                    supply_mult = supply_demand_modifier(qty, entry.get("base_stock", qty))
                    final_price = calculate_buy_price(base_price, rep_mult * shop_price_mod, supply_mult)

                    if player_gold < final_price:
                        return ActionResult(
                            action_id=action.id, success=False,
                            outcome_description=f"You can't afford {item_data.get('name', item_id)} ({final_price} gp). You have {player_gold} gp.",
                        )

                    # Execute the purchase
                    new_gold = player_gold - final_price
                    entry["quantity"] = qty - 1

                    # Update shop stock and gold
                    repos = self._repos or {}
                    shop_repo = repos.get("shop")
                    if shop_repo:
                        shop_repo.update_stock(shop["id"], stock)
                        shop_repo.update_gold_reserve(shop["id"], shop.get("gold_reserve", 500) + final_price)

                    mutations = [
                        StateMutation(
                            target_type="character", target_id=char["id"],
                            field="gold", old_value=player_gold, new_value=new_gold,
                        ),
                        StateMutation(
                            target_type="inventory", target_id=char["id"],
                            field="items_add", old_value=None,
                            new_value=json.dumps({"item_id": item_id, "quantity": 1}),
                        ),
                    ]

                    display = item_data.get("name", item_id)
                    owner_name = self._get_owner_name(shop, context)
                    return ActionResult(
                        action_id=action.id, success=True,
                        outcome_description=f"You buy {display} from {owner_name} for {final_price} gp. ({new_gold} gp remaining)",
                        state_mutations=mutations,
                        events=[{
                            "event_type": "SHOP_BUY",
                            "description": f"Bought {display} for {final_price} gp.",
                            "actor_id": char["id"],
                            "mechanical_details": {"item_id": item_id, "price": final_price, "shop_id": shop["id"]},
                        }],
                    )

        return ActionResult(
            action_id=action.id, success=False,
            outcome_description=f"No shop here sells '{item_name}'. Type 'browse' to see available items.",
        )

    def _resolve_sell(self, action: Action, context: GameContext) -> ActionResult:
        """Sell an item to a shop."""
        item_name = (action.target_id or "").lower().strip()
        if not item_name:
            return ActionResult(action_id=action.id, success=False, outcome_description="Sell what?")

        shops = self._get_shops_at_location(context)
        if not shops:
            return ActionResult(action_id=action.id, success=False, outcome_description="There are no shops here to sell to.")

        # Find item in player inventory
        inv = context.inventory
        if not inv:
            return ActionResult(action_id=action.id, success=False, outcome_description="You don't have any items.")

        items = safe_json(inv.get("items"), [])

        from text_rpg.systems.item_catalog import get_item_catalog
        all_items = get_item_catalog(self._repos, context.game_id, context.character.get("id"))
        found_entry = None
        found_item_data = None

        for entry in items:
            item_id = entry.get("item_id", "")
            item_data = all_items.get(item_id, {})
            display_name = item_data.get("name", item_id).lower()
            if item_name in display_name or display_name in item_name or item_name in item_id.replace("_", " "):
                found_entry = entry
                found_item_data = item_data
                break

        if not found_entry or not found_item_data:
            return ActionResult(action_id=action.id, success=False, outcome_description=f"You don't have '{item_name}'.")

        item_id = found_entry["item_id"]
        base_price = found_item_data.get("value_gp", 0)
        sell_price = calculate_sell_price(base_price)

        if sell_price <= 0:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"{found_item_data.get('name', item_id)} has no resale value.",
            )

        # Check shop can afford it (use first shop)
        shop = shops[0]
        shop_gold = shop.get("gold_reserve", 500)
        if shop_gold < sell_price:
            owner_name = self._get_owner_name(shop, context)
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"{owner_name} doesn't have enough gold to buy that ({sell_price} gp needed, has {shop_gold} gp).",
            )

        char = context.character
        player_gold = char.get("gold", 0)
        new_gold = player_gold + sell_price

        # Update shop gold
        repos = self._repos or {}
        shop_repo = repos.get("shop")
        if shop_repo:
            shop_repo.update_gold_reserve(shop["id"], shop_gold - sell_price)

        mutations = [
            StateMutation(
                target_type="character", target_id=char["id"],
                field="gold", old_value=player_gold, new_value=new_gold,
            ),
            StateMutation(
                target_type="inventory", target_id=char["id"],
                field="items_remove_one", old_value=None, new_value=item_id,
            ),
        ]

        display = found_item_data.get("name", item_id)
        owner_name = self._get_owner_name(shop, context)
        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=f"You sell {display} to {owner_name} for {sell_price} gp. ({new_gold} gp total)",
            state_mutations=mutations,
            events=[{
                "event_type": "SHOP_SELL",
                "description": f"Sold {display} for {sell_price} gp.",
                "actor_id": char["id"],
                "mechanical_details": {"item_id": item_id, "price": sell_price, "shop_id": shop["id"]},
            }],
        )

    def _get_owner_name(self, shop: dict, context: GameContext) -> str:
        """Get the display name of the shop owner."""
        owner_id = shop.get("owner_entity_id", "")
        for e in context.entities:
            if e["id"] == owner_id:
                return e.get("name", "the shopkeeper")
        return "the shopkeeper"
