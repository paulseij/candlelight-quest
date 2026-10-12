"""Canonical, branch-local history. Prose and memory never override live entities."""
from __future__ import annotations

import json


class Continuity:
    def __init__(self, repos):
        self.repos = repos
        self.db = repos['save_game'].db

    def record(self, event, world_time):
        if event['event_type'] not in {
            'DEATH', 'REVIVAL', 'REVIVAL_ATTEMPT', 'REST', 'QUEST_COMPLETE', 'QUEST_FAILED',
            'LOCATION_DESTROYED', 'LOCATION_REBUILT',
            'CAMPAIGN_STARTED', 'CAMPAIGN_EVIDENCE', 'CAMPAIGN_CHOICE',
            'CAMPAIGN_CONSEQUENCE', 'CAMPAIGN_COMPLETE', 'PERSONAL_GOAL',
        }:
            return
        details = event.get('mechanical_details') or {}
        if isinstance(details, str):
            details = json.loads(details)
        with self.db.get_connection() as conn:
            conn.execute('INSERT OR IGNORE INTO world_history VALUES (?,?,?,?,?,?,?,?)', (
                event['game_id'], event['id'], event.get('target_id'), event['event_type'],
                details.get('cause_id'), world_time, event.get('description', ''), json.dumps(details)))
            if event['event_type'] in {'LOCATION_DESTROYED', 'LOCATION_REBUILT'}:
                target = event.get('target_id')
                if not conn.execute('SELECT 1 FROM locations WHERE game_id=? AND id=?', (event['game_id'], target)).fetchone():
                    raise ValueError('World change requires an existing location')
                condition = 'destroyed' if event['event_type'] == 'LOCATION_DESTROYED' else 'rebuilt'
                conn.execute('INSERT INTO location_conditions VALUES (?,?,?,?) ON CONFLICT(game_id,location_id) DO UPDATE SET condition=excluded.condition,cause_event_id=excluded.cause_event_id',
                             (event['game_id'], target, condition, event['id']))

    def location_condition(self, game_id, location_id):
        with self.db.get_connection() as conn:
            row = conn.execute('SELECT condition,cause_event_id FROM location_conditions WHERE game_id=? AND location_id=?', (game_id, location_id)).fetchone()
        return dict(row) if row else None

    def facts(self, game_id, location_id, mentioned='', include_history=True):
        """Live status wins over historical statements, including pre-death dialogue."""
        lines = []
        for entity in self.repos['entity'].get_by_game(game_id):
            name = entity.get('name', entity['id'])
            if entity.get('entity_type') != 'npc':
                continue
            if entity.get('location_id') != location_id and name.lower() not in mentioned.lower():
                continue
            status = 'alive' if entity.get('is_alive', True) else 'dead; cannot speak, work, travel or give quests'
            lines.append(f"{name}: {status}.")
            from text_rpg.utils import safe_props
            revival = safe_props(entity).get('last_revival')
            if revival and entity.get('is_alive', True):
                lines.append(f"{name}'s recorded return: {revival['method']}; {revival.get('consequence', '')}.")
        if include_history:
            with self.db.get_connection() as conn:
                rows = conn.execute('SELECT * FROM world_history WHERE game_id=? ORDER BY world_time DESC, rowid DESC LIMIT 12', (game_id,)).fetchall()
            # Historical occurrences are labelled explicitly; they are not live state.
            lines.extend(f"Recorded history (game minute {r['world_time']}): {r['description']}" for r in reversed(rows))
        with self.db.get_connection() as conn:
            places = conn.execute("SELECT lc.location_id,l.name,lc.condition FROM location_conditions lc JOIN locations l ON l.game_id=lc.game_id AND l.id=lc.location_id WHERE lc.game_id=?", (game_id,)).fetchall()
        for place in places:
            if place['location_id'] == location_id or place['name'].lower() in mentioned.lower():
                lines.append(f"{place['name']}: currently {place['condition']}.")
        return '\n'.join(lines)
