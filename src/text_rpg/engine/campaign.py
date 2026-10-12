"""Deterministic campaign progression, with persistent evidence and causal aftermath.

The model can describe this state but cannot create, resolve or reset an incident.
All mutations run inside the turn's transaction; observational methods only read.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import random
import secrets
import uuid

from text_rpg.content.campaigns import CAMPAIGNS, PERSONAL_GOALS
from text_rpg.engine.continuity import Continuity
from text_rpg.engine.exits import exit_options
from text_rpg.models.action import ActionResult


class CampaignRuntime:
    def __init__(self, repos):
        self.repos = repos
        self.db = repos['save_game'].db

    def get(self, game_id):
        game = self.repos['save_game'].get_game(game_id)
        location = self.repos['location'].get(game['current_location_id'], game_id) if game else None
        region = (location or {}).get('region_id')
        if not region:
            return None
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT data FROM campaign_instances WHERE game_id=? AND id=?", (game_id, region)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, game_id, state):
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO campaign_instances VALUES (?,?,?) ON CONFLICT(game_id,id) DO UPDATE SET data=excluded.data", (game_id, state['region'], json.dumps(state)))

    def route(self, game_id, start, target):
        queue = deque([(start, [])]); seen = {start}
        while queue:
            location, path = queue.popleft()
            if location == target:
                return path
            connections = self.repos['connection'].get_connections(game_id, location)
            for selector, connection in exit_options(connections):
                nxt = connection['target_location_id']
                if connection.get('is_locked') or connection.get('is_hidden') or nxt in seen:
                    continue
                seen.add(nxt)
                queue.append((nxt, path + [selector]))
        return None

    def _regional_template(self, game_id, region_id, current_location_id):
        """Compile a grounded two-chapter investigation for any authored region."""
        from text_rpg.content.loader import load_all_regions

        region = load_all_regions().get(region_id, {})
        region_name = region.get('name', region_id.replace('_', ' ').title())
        locations = self.repos['location'].get_by_region(game_id, region_id)
        if not locations:
            return None
        hub = next((loc for loc in locations
                    if loc.get('location_type') in {'town', 'settlement'} and
                    self.route(game_id, current_location_id, loc['id']) is not None and
                    self.route(game_id, loc['id'], current_location_id) is not None), None)
        hub = hub or next((loc for loc in locations if loc['id'] == current_location_id), None)
        if not hub:
            return None
        candidates = [loc for loc in locations if loc['id'] != hub['id'] and
                      self.route(game_id, current_location_id, loc['id']) is not None and
                      self.route(game_id, loc['id'], hub['id']) is not None]
        # Prefer different kinds of places before taking a second of one kind.
        selected = []
        for duplicate_pass in (False, True):
            for loc in candidates:
                already_kind = any(item.get('location_type') == loc.get('location_type') for item in selected)
                if already_kind == duplicate_pass and loc not in selected:
                    selected.append(loc)
                if len(selected) == 6:
                    break
            if len(selected) == 6:
                break
        if len(selected) < 4:
            return None

        labels = {
            'town': 'civic notice', 'settlement': 'public register', 'shop': 'stock ledger',
            'tavern': 'guest tally', 'temple': 'offering record', 'dungeon': 'abandoned notation',
            'wilderness': 'survey marks', 'road': 'travel register', 'ruins': 'weathered inscription',
            'residence': 'household account',
        }
        site_rows = []
        for index, loc in enumerate(selected):
            record = labels.get(loc.get('location_type'), 'local record')
            if index < 3:
                finding = (f"The {record} at {loc['name']} contradicts the regional notice: "
                           "a date was changed after filing, beside the same three-notch mark. "
                           "This is an independent surviving record, not an accusation by itself.")
            else:
                finding = (f"The {record} at {loc['name']} preserves a matching three-notch mark and a route code. "
                           "Its date links the earlier discrepancy to a second set of instructions. "
                           "Together with another source, it can establish what happened.")
            site_rows.append([loc['id'], record, finding])

        location_ids = {loc['id'] for loc in locations}
        contacts = [entity['id'] for entity in self.repos['entity'].get_by_game(game_id)
                    if entity.get('location_id') in location_ids and entity.get('entity_type') == 'npc'
                    and entity.get('is_alive', True) and not entity.get('generated')][:3]
        return {
            'title': f'The {region_name} Discrepancy', 'hub': hub['id'],
            'motives': [
                f'The records suggest someone profited by rerouting ordinary supplies through {region_name}.',
                f'The records suggest a local office hid repeated failures before a regional inspection.',
                f'The records suggest competing interests altered instructions to discredit one another.',
            ],
            'contacts': contacts,
            'opening': (f'Public records across {region_name} no longer agree. No single page proves wrongdoing, '
                        f'but independent copies may reveal whether the changes were error or design. The filing point is {hub["name"]}.'),
            'chapters': [
                {'title': 'The altered notice', 'sites': site_rows[:3],
                 'expose': 'You publish the matching dates and marks. The disputed instructions can no longer pass unnoticed, and a route code points to a second trail.',
                 'secure': 'You file verified copies and suspend the disputed instructions. The immediate risk is contained, and a route code points to a second trail.',
                 'pressure': ['Another public notice repeats the disputed instruction. The independent records remain available.',
                              'Local routines begin adapting to the bad instruction. The surviving copies still preserve the discrepancy.']},
                {'title': 'The route behind the record', 'sites': site_rows[3:6],
                 'expose': 'You publish the matching route codes and dates. The full alteration trail becomes part of the regional record, closing the investigation.',
                 'secure': 'You file the matching route codes and replace the compromised instructions. The evidence remains preserved, closing the investigation.',
                 'pressure': ['A revised instruction changes the route code, but the earlier copies remain on file.',
                              'The disputed process is being withdrawn. Your preserved records prevent the old trail from disappearing.']},
            ],
        }

    def ensure(self, game_id):
        """Introduce one validated scenario, once. Never change an existing save's facts."""
        with self.db.transaction():
            if self.get(game_id):
                return False
            game = self.repos['save_game'].get_game(game_id)
            location = self.repos['location'].get(game['current_location_id'], game_id)
            region_id = (location or {}).get('region_id')
            template = CAMPAIGNS.get(region_id) or self._regional_template(
                game_id, region_id, game['current_location_id'])
            if not template:
                return False
            # Compile only into a graph where all required sites can be reached
            # and the player can return. A partially generated map cannot strand it.
            required = {template['hub']}
            required.update(site[0] for chapter in template['chapters'] for site in chapter['sites'])
            if any(not self.repos['location'].get(site, game_id) or
                   self.route(game_id, game['current_location_id'], site) is None or
                   self.route(game_id, site, template['hub']) is None for site in required):
                return False
            char = self.repos['character'].get_by_game(game_id) or {}
            scenario = deepcopy(template)
            variant_seed = secrets.randbits(64)
            rng = random.Random(variant_seed)
            motive = rng.choice(scenario.pop('motives'))
            dangerous = {'smuggler_den', 'deep_mines', 'old_mine_shaft'}
            for chapter in scenario['chapters']:
                safer = [site for site in chapter['sites'] if site[0] not in dangerous]
                hazardous = [site for site in chapter['sites'] if site[0] in dangerous]
                rng.shuffle(safer); rng.shuffle(hazardous)
                chapter['sites'] = safer + hazardous
            for site in scenario['chapters'][1]['sites']:
                site[2] += ' ' + motive
            state = {
                'version': 2, 'variant_seed': variant_seed, 'motive': motive,
                'template': scenario, 'region': location['region_id'],
                'stage': 0, 'status': 'active', 'evidence': {}, 'choices': [],
                'started_at': game['world_time'], 'stage_started_at': game['world_time'],
                'notices': [], 'applied': [], 'known_deaths': [], 'reactions': [],
                'goal': 'knowledge' if char.get('char_class') in {'wizard', 'sorcerer', 'warlock'} else 'community',
                'origin_id': char.get('origin_id'),
            }
            event = self.event('CAMPAIGN_STARTED', template['opening'], 'regional', cause_id=None, variant_seed=variant_seed)
            self._append(game_id, event)
            state['cause_id'] = event['id']
            self.save(game_id, state)
            self._create_quest(game_id, state)
            return True

    def _append(self, game_id, event):
        game = self.repos['save_game'].get_game(game_id)
        row = {**event, 'game_id': game_id, 'turn_number': game['turn_number'],
               'location_id': game['current_location_id'], 'timestamp': datetime.now(timezone.utc).isoformat(),
               'mechanical_details': json.dumps(event['mechanical_details']), 'is_canonical': True}
        self.repos['event_ledger'].append(row)
        Continuity(self.repos).record(row, game['world_time'])

    @staticmethod
    def event(kind, text, target='regional', **details):
        return {'id': str(uuid.uuid4()), 'event_type': kind, 'target_id': target,
                'description': text, 'mechanical_details': details}

    @staticmethod
    def quest_id(state):
        if state.get('version', 1) >= 2:
            return f"campaign_{state['region']}_regional_{state['stage']}"
        return f"campaign_regional_{state['stage']}"

    def _create_quest(self, game_id, state):
        chapter = state['template']['chapters'][state['stage']]
        self.repos['world_state'].save_quest({
            'id': self.quest_id(state), 'game_id': game_id, 'name': chapter['title'],
            'description': state['template']['opening'] if state['stage'] == 0 else 'Follow the surviving records identified by your first investigation. Use leads for known evidence and routes.',
            'status': 'active', 'quest_giver_id': None, 'xp_reward': 75, 'gold_reward': 20,
            'item_rewards': [], 'completion_flexibility': 'none',
            'objectives': [
                {'id': 'evidence', 'description': 'Document two independent pieces of evidence (use leads)', 'objective_type': 'campaign_evidence', 'target_count': 2, 'current_count': 0, 'is_complete': False},
                {'id': 'decision', 'description': 'Return to the notice board: resolve expose OR resolve secure', 'objective_type': 'campaign_choice', 'target_count': 1, 'current_count': 0, 'is_complete': False},
            ],
        })

    @staticmethod
    def is_command(raw):
        return bool(re.fullmatch(r'(?:leads|campaign|campaign journal|history|personal goal(?:\s+\w+)?|resolve(?:\s+\w+)?|investigate incident)', raw.strip(), re.I))

    def recognizes(self, raw, context):
        if self.is_command(raw):
            return True
        state = self.get(context.game_id)
        if not state:
            return False
        text = raw.strip().lower().rstrip('.!?')
        return any(site[0] == context.location['id'] and text in {
            f'inspect {site[1]}', f'examine {site[1]}', f'investigate {site[1]}'
        } for site in state['template']['chapters'][state['stage']]['sites'])

    def handle(self, raw, context):
        state = self.get(context.game_id)
        text = raw.strip().lower().rstrip('.!?')
        if text in {'leads', 'campaign', 'campaign journal'}:
            return ActionResult(success=True, outcome_description=self.journal(context.game_id))
        if text == 'history':
            with self.db.get_connection() as conn:
                rows = conn.execute('SELECT world_time,description FROM world_history WHERE game_id=? ORDER BY world_time, rowid', (context.game_id,)).fetchall()
            return ActionResult(success=True, outcome_description='\n\n'.join(f"Minute {r[0]}: {r[1]}" for r in rows) or 'No major events recorded yet.')
        if not state:
            return ActionResult(outcome_description='No regional investigation is active here. Use quests for local work.')
        chapter = state['template']['chapters'][state['stage']]
        site = next((s for s in chapter['sites'] if s[0] == context.location['id']), None)
        inspect = bool(site and text in {f'inspect {site[1]}', f'examine {site[1]}', f'investigate {site[1]}', 'investigate incident'})
        if not (inspect or text.startswith('resolve') or text.startswith('personal goal')):
            return ActionResult(outcome_description='There is no current investigation record here. Use leads for locations and commands.')
        if text == 'personal goal':
            return ActionResult(success=True, outcome_description='Choose with personal goal <name>: ' + '; '.join(f'{k}: {v}' for k,v in PERSONAL_GOALS.items()))
        if context.combat_state and context.combat_state.get('is_active'):
            return ActionResult(outcome_description='Finish or escape the encounter before working on the investigation.')
        if state['status'] != 'active':
            return ActionResult(outcome_description='This campaign is no longer active. Its evidence and consequences remain in campaign and history.')
        if text.startswith('personal goal '):
            goal = text.removeprefix('personal goal ')
            if goal not in PERSONAL_GOALS:
                return ActionResult(outcome_description='Choose knowledge, community, justice, or discretion.')
            if goal == state['goal']:
                return ActionResult(outcome_description='That is already your personal goal.')
            state['goal'] = goal
            self.save(context.game_id, state)
            event = self.event('PERSONAL_GOAL', f"You choose a personal goal: {PERSONAL_GOALS[goal]}", cause_id=state['cause_id'])
            return ActionResult(success=True, outcome_description=event['description'], events=[event])
        if inspect:
            key = f"{state['stage']}:{site[0]}"
            if key in state['evidence']:
                return ActionResult(success=True, outcome_description='Already recorded: ' + state['evidence'][key]['text'])
            from text_rpg.engine.continuity import Continuity
            condition = Continuity(self.repos).location_condition(context.game_id, site[0])
            if condition and condition['condition'] == 'destroyed':
                return ActionResult(outcome_description='This archive was destroyed. Its record is inaccessible; use another independent lead in leads.')
            state['evidence'][key] = {'location_id': site[0], 'name': site[1], 'text': site[2]}
            event = self.event('CAMPAIGN_EVIDENCE', site[2], site[0], cause_id=state['cause_id'], quest_id=self.quest_id(state), objective_id='evidence', evidence_id=key)
            state['evidence'][key]['event_id'] = event['id']
            self.save(context.game_id, state)
            return ActionResult(success=True, outcome_description=site[2] + '\n\nEvidence recorded. Use leads to plan your next step.', events=[event])
        choice = text.removeprefix('resolve').strip()
        if choice not in {'expose', 'secure'}:
            return ActionResult(outcome_description='Use resolve expose to publish the evidence, or resolve secure to file safeguards privately.')
        filing_site = self.decision_hub(context.game_id, state)
        if context.location['id'] != filing_site:
            return ActionResult(outcome_description=f"Bring your evidence to {self.location_name(context.game_id, filing_site)}. Use leads for a route.")
        if sum(k.startswith(f"{state['stage']}:") for k in state['evidence']) < 2:
            return ActionResult(outcome_description='You need two independent pieces of evidence before taking action. Use leads.')
        event = self.event('CAMPAIGN_CHOICE', chapter[choice], cause_id=state['cause_id'], quest_id=self.quest_id(state), objective_id='decision', choice=choice)
        state['choices'].append(choice)
        state['cause_id'] = event['id']
        self.save(context.game_id, state)
        return ActionResult(success=True, outcome_description=chapter[choice], events=[event])

    def advance(self, context):
        """No LLM, no random respawns. Each consequence has a durable once-only key."""
        state = self.get(context.game_id)
        if not state:
            return []
        events = []
        for npc_id in state['template']['contacts']:
            npc = self.repos['entity'].get(npc_id, context.game_id)
            if not npc:
                continue
            dead = not npc.get('is_alive', True)
            was_dead = npc_id in state['known_deaths']
            if dead == was_dead:
                continue
            if dead:
                state['known_deaths'].append(npc_id)
                text = f"{npc['name']} is dead and can no longer help. Their surviving public records remain available; the investigation does not require a living quest giver."
            else:
                state['known_deaths'].remove(npc_id)
                text = f"{npc['name']} is alive again. Their earlier death remains part of the community's history."
            with self.db.get_connection() as conn:
                cause = conn.execute("SELECT id FROM world_history WHERE game_id=? AND subject_id=? AND event_type IN ('DEATH','REVIVAL') ORDER BY rowid DESC LIMIT 1", (context.game_id, npc_id)).fetchone()
            events.append(self.event('CAMPAIGN_CONSEQUENCE', text, npc_id, cause_id=cause[0] if cause else state['cause_id']))
            state['reactions'].append(text)

        if state['status'] == 'active':
            quest = self.repos['world_state'].get_quest(self.quest_id(state), context.game_id)
            if quest and quest['status'] == 'completed':
                chapter = state['template']['chapters'][state['stage']]
                completed_stage = state['stage']
                effect_id = f'reputation:{completed_stage}'
                if effect_id not in state['applied'] and self.repos.get('reputation'):
                    faction = ('saltmere_port_authority' if state['region'] == 'iron_coast' else
                               'cinderhold_miners' if state['region'] == 'ashen_highlands' else None)
                    if not faction:
                        for contact_id in state['template']['contacts']:
                            contact = self.repos['entity'].get(contact_id, context.game_id)
                            if contact and contact.get('faction_id'):
                                faction = contact['faction_id']
                                break
                    state['applied'].append(effect_id)
                    if faction:
                        delta = 5 if state['choices'][-1] == 'expose' else 2
                        self.repos['reputation'].adjust_faction_rep(context.game_id, faction, delta)
                        events.append(self.event('CAMPAIGN_CONSEQUENCE',
                            f"Your documented decision changes your standing with {faction.replace('_', ' ')} by +{delta}.",
                            cause_id=state['cause_id'], effect_id=effect_id, faction_id=faction, reputation_delta=delta))
                state['notices'] = [chapter[state['choices'][-1]]]
                state['reactions'].append(state['notices'][0])
                if state['stage'] + 1 < len(state['template']['chapters']):
                    state['stage'] += 1
                    state['stage_started_at'] = context.world_time
                    self._create_quest(context.game_id, state)
                    choice = state['choices'][-1]
                    text = ('Your public dossier exposes a payment trail.' if choice == 'expose' else 'The records you preserved contain a payment trail.') + ' The next chapter is ready: ' + state['template']['chapters'][state['stage']]['title'] + '. Use leads.'
                    events.append(self.event('CAMPAIGN_CONSEQUENCE', text, cause_id=state['cause_id']))
                else:
                    state['status'] = 'completed'
                    state['completed_at'] = context.world_time
                    state['personal_complete'] = (state['goal'] in {'knowledge', 'community'} or
                                                  all(c == ('expose' if state['goal'] == 'justice' else 'secure') for c in state['choices']))
                    text = state['template']['title'] + ' is resolved. ' + ('Your personal goal is fulfilled.' if state['personal_complete'] else 'Your choices took a different path from your personal goal.')
                    events.append(self.event('CAMPAIGN_COMPLETE', text, cause_id=state['cause_id']))
            elif not (context.combat_state and context.combat_state.get('is_active')):
                chapter = state['template']['chapters'][state['stage']]
                from text_rpg.engine.continuity import Continuity
                continuity = Continuity(self.repos)
                surviving = {site[0] for site in chapter['sites'] if not (condition := continuity.location_condition(context.game_id, site[0])) or condition['condition'] != 'destroyed'}
                recorded = {key.split(':', 1)[1] for key in state['evidence'] if key.startswith(f"{state['stage']}:")}
                if len(surviving | recorded) < 2:
                    self.repos['world_state'].update_quest_status(self.quest_id(state), context.game_id, 'failed')
                    state['status'] = 'disrupted'
                    text = f"{chapter['title']} cannot continue: too many independent records were destroyed. The investigation is suspended and its history remains."
                    state['reactions'].append(text)
                    events.append(self.event('CAMPAIGN_CONSEQUENCE', text, cause_id=state['cause_id']))
                    self.save(context.game_id, state)
                    return events
                elapsed = context.world_time - state['stage_started_at']
                for index, threshold in enumerate((90, 180)):
                    key = f"pressure:{state['stage']}:{index}"
                    if elapsed >= threshold and key not in state['applied']:
                        state['applied'].append(key)
                        state['notices'].append(chapter['pressure'][index])
                        events.append(self.event('CAMPAIGN_CONSEQUENCE', chapter['pressure'][index], cause_id=state['cause_id'], effect_id=key))
        elif state['status'] == 'completed':
            elapsed = context.world_time - state.get('completed_at', context.world_time)
            followups = (
                ('aftercare:1', 1440,
                 'A new public notice refers back to the evidence you exposed. Attempts to dispute it must answer the surviving records.' if state['choices'][-1] == 'expose' else
                 'A fresh irregularity reaches the filing office. Your preserved records identify it before another unsafe instruction can be issued.'),
                ('aftercare:2', 4320,
                 'Local workers update their procedures using the findings from your investigation. The old problem has changed the way the community operates.'),
            )
            for effect_id, threshold, description in followups:
                if elapsed >= threshold and effect_id not in state['applied']:
                    state['applied'].append(effect_id)
                    state['notices'].append(description)
                    state['reactions'].append(description)
                    events.append(self.event('CAMPAIGN_CONSEQUENCE', description,
                                             cause_id=state['cause_id'], effect_id=effect_id))
        self.save(context.game_id, state)
        return events

    def location_name(self, game_id, location_id):
        location = self.repos['location'].get(location_id, game_id)
        return location['name'] if location else location_id

    def decision_hub(self, game_id, state):
        from text_rpg.engine.continuity import Continuity
        continuity = Continuity(self.repos)
        hub = state['template']['hub']
        condition = continuity.location_condition(game_id, hub)
        if not condition or condition['condition'] != 'destroyed':
            return hub
        for connection in self.repos['connection'].get_connections(game_id, hub):
            alternate = connection['target_location_id']
            alternate_condition = continuity.location_condition(game_id, alternate)
            if not connection.get('is_locked') and (not alternate_condition or alternate_condition['condition'] != 'destroyed'):
                return alternate
        return hub

    def contact_hint(self, game_id, npc_id):
        state = self.get(game_id)
        if not state or state['status'] != 'active' or npc_id not in state['template']['contacts']:
            return ''
        chapter = state['template']['chapters'][state['stage']]
        remaining = [site for site in chapter['sites'] if f"{state['stage']}:{site[0]}" not in state['evidence']]
        if remaining:
            site = remaining[0]
            return f"Current public investigation: {chapter['title']}. A documented lead is {site[1]} at {self.location_name(game_id, site[0])}. No private evidence or culprit identity is known to this NPC."
        return f"Current public investigation: {chapter['title']}. Records can be filed or published at {self.location_name(game_id, state['template']['hub'])}. No private evidence or culprit identity is known to this NPC."

    def scene(self, game_id, location_id):
        state = self.get(game_id)
        if not state:
            return ''
        chapter = state['template']['chapters'][state['stage']]
        lines = []
        site = next((s for s in chapter['sites'] if s[0] == location_id), None)
        if site and state['status'] == 'active':
            from text_rpg.engine.continuity import Continuity
            condition = Continuity(self.repos).location_condition(game_id, location_id)
            if condition and condition['condition'] == 'destroyed':
                lines.append(f"The {site[1]} archive was destroyed. Consult leads for surviving sources.")
            else:
                lines.append(f"Investigation lead: {site[1]}. You can inspect {site[1]}.")
        if location_id == self.decision_hub(game_id, state):
            lines.extend(state['notices'][-2:])
            lines.append('Campaign: ' + state['template']['title'] + (' — resolved.' if state['status'] == 'completed' else '. Use leads for your next steps.'))
        return '\n\n'.join(lines)

    def journal(self, game_id):
        state = self.get(game_id)
        if not state:
            return 'No regional campaign has been introduced here yet. Use quests for local work and talk to nearby NPCs.'
        template = state['template']; chapter = template['chapters'][state['stage']]
        game = self.repos['save_game'].get_game(game_id)
        lines = [f"{template['title']} — {state['status']}", template['opening'],
                 'Personal goal: ' + PERSONAL_GOALS[state['goal']],
                 'You can change your goal with personal goal knowledge/community/justice/discretion.']
        if state['status'] == 'active':
            lines.append(f"Chapter {state['stage']+1}: {chapter['title']} — gather two independent records, then choose a response. Reward: 75 XP, 20 gold.")
            from text_rpg.engine.continuity import Continuity
            continuity = Continuity(self.repos)
            for site, name, _ in chapter['sites']:
                recorded = f"{state['stage']}:{site}" in state['evidence']
                condition = continuity.location_condition(game_id, site)
                path = self.route(game_id, game['current_location_id'], site)
                directions = ('here' if path == [] else ' → '.join('go ' + p for p in path)) if path is not None else 'no currently open route'
                status = 'Recorded' if recorded else ('Lost to destruction' if condition and condition['condition'] == 'destroyed' else 'Lead')
                risk = ' Possible hostile encounter.' if site in {'smuggler_den', 'deep_mines', 'old_mine_shaft'} else ''
                lines.append(f"{status}: {name} — {self.location_name(game_id, site)} ({directions}). Command there: inspect {name}.{risk}")
            filing_site = self.decision_hub(game_id, state)
            path = self.route(game_id, game['current_location_id'], filing_site)
            route = ('here' if path == [] else ' → '.join('go ' + p for p in path)) if path is not None else 'no currently open route'
            lines.append(f"Decision at {self.location_name(game_id, filing_site)} ({route}): resolve expose publishes the evidence; resolve secure files safeguards privately.")
            lines.append('No combat is required. Public records remain accessible if a contact dies. Dangerous routes can still contain encounters.')
        else:
            lines.append('Personal goal: ' + ('fulfilled.' if state.get('personal_complete') else 'unfinished.' if state['status'] == 'disrupted' else 'not fulfilled; your choices followed another path.'))
        contacts = []
        for npc_id in template['contacts']:
            npc = self.repos['entity'].get(npc_id, game_id)
            if npc:
                status = self.location_name(game_id, npc['location_id']) if npc.get('is_alive', True) else 'dead'
                contacts.append(npc['name'] + ' — ' + status)
        if contacts:
            lines.append('Local contacts: ' + '; '.join(contacts))
        if state['evidence']:
            lines.append('Your evidence:\n' + '\n'.join(e['name'] + ': ' + e['text'] for e in state['evidence'].values()))
        lines.extend(state['reactions'][-3:] + state['notices'][-2:])
        return '\n\n'.join(lines)
