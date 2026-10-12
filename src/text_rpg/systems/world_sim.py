"""World simulator — runs once per turn to advance NPC schedules and ambient state."""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from typing import Any

from text_rpg.mechanics import world_clock
from text_rpg.mechanics.world_sim import get_npc_location, is_npc_available
from text_rpg.mechanics.revival import REVIVAL_RITES, revival_block_reason
from text_rpg.utils import safe_json, safe_props

logger = logging.getLogger(__name__)

class WorldSimulator:
    """Lightweight system called once per turn (not a GameSystem)."""

    def __init__(self, repos: dict[str, Any]) -> None:
        self.repos = repos
        self._last_period: str | None = None

    def tick(self, game_id: str, world_time: int) -> list[dict]:
        """Advance world state based on time. Returns events to record."""
        period = world_clock.get_period(world_time)

        # Only move NPCs when the period actually changes
        if period == self._last_period:
            return []
        self._last_period = period

        events: list[dict] = []
        events += self._update_npc_locations(game_id, period)
        events += self._rare_revival(game_id, world_time)
        self._cleanup_expired_entities(game_id, world_time)
        self._update_shop_prices_from_events(game_id)
        return events

    def _rare_revival(self, game_id: str, world_time: int) -> list[dict]:
        """A funded, present caster may attempt a rare offscreen revival.

        A day is checked once in the current save branch, even after restart.
        The turn loop writes the REVIVAL event before changing the entity's
        current life state, in one transaction.
        """
        from text_rpg.storage.database import Database
        db = getattr(self.repos.get('save_game'), 'db', None)
        if not isinstance(db, Database) or world_time < 3 * 1440:
            return []
        minute = world_time % 1440
        if not 480 <= minute < 540:
            return []
        day = world_time // 1440
        with db.get_connection() as conn:
            checked = conn.execute("INSERT OR IGNORE INTO world_event_checks VALUES (?, 'rare_revival', ?)", (game_id, day))
            if not checked.rowcount:
                return []
            recent = conn.execute("SELECT 1 FROM world_history WHERE game_id=? AND event_type IN ('REVIVAL','REVIVAL_ATTEMPT') AND world_time>? LIMIT 1",
                                  (game_id, world_time - 30 * 1440)).fetchone()
            if recent:
                return []
            deaths = {r['subject_id']: (r['id'], r['world_time']) for r in conn.execute(
                "SELECT id,subject_id,world_time FROM world_history WHERE game_id=? AND event_type='DEATH' ORDER BY world_time", (game_id,))}
        entities = self.repos['entity'].get_by_game(game_id)
        candidates = [e for e in entities
                      if not e.get('is_alive', True)
                      and not safe_props(e).get('expires_at_time')
                      and e['id'] in deaths and world_time - deaths[e['id']][1] >= 3 * 1440]
        # Rites require authored, persistent resources. A generated description
        # saying "powerful healer" grants neither resources nor permission.
        pairs = []
        from text_rpg.engine.continuity import Continuity
        for source in entities:
            if not source.get('is_alive', True) or source.get('generated'):
                continue
            stock = safe_props(source).get('revival_resources', {})
            method = stock.get('method')
            rite = REVIVAL_RITES.get(method)
            if not rite or source.get('level', 1) < rite.min_level:
                continue
            if stock.get('charges', 0) < 1 or stock.get('materials_gp', 0) < rite.materials_gp or stock.get('ready_at', 0) > world_time:
                continue
            condition = Continuity(self.repos).location_condition(game_id, source['location_id'])
            if condition and condition['condition'] == 'destroyed':
                continue
            for target in candidates:
                if source['location_id'] != target['location_id']:
                    continue
                # A source must have a reason to help: an authored target or
                # a shared, nonempty faction. No random revival of strangers.
                if target['id'] not in stock.get('target_ids', []) and not (source.get('faction_id') and source.get('faction_id') == target.get('faction_id')):
                    continue
                age = world_time - deaths[target['id']][1]
                if revival_block_reason(target, age, rite.max_age_minutes, restores_body=rite.restores_body):
                    continue
                pairs.append((source, target, stock, method, rite))
        if not pairs or secrets.randbelow(1000) >= 8:
            return []
        source, target, stock, method, rite = secrets.choice(pairs)
        from text_rpg.mechanics.ability_scores import modifier
        scores = safe_json(source.get('ability_scores'), {})
        bonus = modifier(scores.get(rite.ability, 10)) + 2 + (source['level'] - 1) // 4
        dc, aftermath = rite.dc, rite.consequence
        from text_rpg.mechanics.dice import roll_d20
        check = roll_d20(bonus)
        passed = check.total >= dc
        # Both outcomes consume the reserved material stock and ritual charge.
        # Save/reload and describing the rite differently cannot refund it.
        props = safe_props(source)
        props['revival_resources'] = {**stock, 'charges': stock['charges'] - 1,
                                     'materials_gp': stock['materials_gp'] - rite.materials_gp,
                                     'ready_at': world_time + 30 * 1440}
        self.repos['entity'].update_field(source['id'], 'properties', props, game_id)
        death_id, _ = deaths[target['id']]
        location = self.repos['location'].get(target['location_id'], game_id)
        place = location['name'] if location else 'an old battleground'
        outcome = (f'{target["name"]} is revived with 1 HP, bearing {aftermath}' if passed
                   else f'{target["name"]} remains dead; the ritual materials and charge are lost')
        return [{'id': str(uuid.uuid4()), 'event_type': 'REVIVAL' if passed else 'REVIVAL_ATTEMPT',
                 'actor_id': source['id'],
                 'target_id': target['id'], 'location_id': target['location_id'],
                 'description': f'{source["name"]} performs {rite.name} near {place}, consuming {rite.materials_gp} gp of reserved materials. The check is {check.total} against DC {dc}: {outcome}.',
                 'mechanical_details': {'cause_id': death_id, 'method': method, 'roll': check.individual_rolls[0],
                                        'modifier': bonus, 'total': check.total, 'dc': dc,
                                        'materials_gp': rite.materials_gp, 'source_id': source['id'],
                                        'restores_body': rite.restores_body,
                                        'charge_consumed': 1, 'consequence': aftermath if passed else 'ritual resources consumed; target remains dead',
                                        'restored_hp': 1 if passed else 0}}]

    def _update_npc_locations(self, game_id: str, period: str) -> list[dict]:
        """Move NPCs with schedules to their current scheduled location."""
        events: list[dict] = []
        try:
            entity_repo = self.repos.get("entity")
            if not entity_repo:
                return events

            # Get all NPCs for this game
            all_npcs = entity_repo.get_by_game(game_id)
            for npc in all_npcs:
                if npc.get("entity_type") != "npc" or not npc.get("is_alive", True):
                    continue

                scheduled_loc = get_npc_location(npc, period)
                if scheduled_loc is None:
                    continue  # NPC is unavailable (sleeping etc.)
                from text_rpg.storage.database import Database
                if isinstance(getattr(self.repos.get('save_game'), 'db', None), Database):
                    from text_rpg.engine.continuity import Continuity
                    condition = Continuity(self.repos).location_condition(game_id, scheduled_loc)
                    if condition and condition['condition'] == 'destroyed':
                        continue

                current_loc = npc.get("location_id", "")
                if current_loc != scheduled_loc:
                    # Move the NPC
                    entity_repo.update_field(npc["id"], "location_id", scheduled_loc)
                    logger.debug(f"NPC {npc['name']} moved from {current_loc} to {scheduled_loc}")
        except Exception as e:
            logger.warning(f"NPC schedule update failed: {e}")

        return events

    def _update_shop_prices_from_events(self, game_id: str) -> None:
        """Check for world events that affect shop prices."""
        try:
            shop_repo = self.repos.get("shop")
            event_repo = self.repos.get("event_ledger")
            if not shop_repo or not event_repo:
                return
            recent = event_repo.get_recent(game_id, limit=5)
            for event in recent:
                details = safe_json(event.get("mechanical_details"), {})
                if not details:
                    continue
                effect = details.get("economy_effect")
                if effect == "trade_route_disrupted":
                    # Increase prices at all shops temporarily
                    location_id = event.get("location_id", "")
                    if location_id:
                        shops = shop_repo.get_shop_by_location(game_id, location_id)
                        for s in shops:
                            if s.get("price_modifier", 1.0) < 1.3:
                                shop_repo.update_price_modifier(s["id"], 1.3)
                elif effect == "trade_route_restored":
                    location_id = event.get("location_id", "")
                    if location_id:
                        shops = shop_repo.get_shop_by_location(game_id, location_id)
                        for s in shops:
                            if s.get("price_modifier", 1.0) > 1.0:
                                shop_repo.update_price_modifier(s["id"], 1.0)
        except Exception as e:
            logger.warning(f"Shop price update failed: {e}")

    def _cleanup_expired_entities(self, game_id: str, world_time: int) -> None:
        """Remove temporary entities whose duration has expired."""
        try:
            entity_repo = self.repos.get("entity")
            if not entity_repo:
                return

            all_entities = entity_repo.get_by_game(game_id)
            for entity in all_entities:
                props = safe_props(entity)
                if not props:
                    continue

                expires_at = props.get("expires_at_time")
                if expires_at is not None and world_time >= expires_at:
                    # Remove the temporary entity
                    entity_repo.update_field(entity["id"], "is_alive", False)
                    logger.debug(f"Temporary entity {entity.get('name')} expired and removed")
        except Exception as e:
            logger.warning(f"Entity cleanup failed: {e}")
