"""Render binding game facts from typed records, never model summaries."""
from text_rpg.utils import safe_json


def objective_text(objective):
    count = max(1,int(objective.get('target_count') or 1))
    kind = objective.get('objective_type')
    if kind == 'kill':
        return f"Defeat {str(objective.get('target_entity_type','foes')).replace('_',' ')} ({count} required)"
    if kind == 'travel':
        return f"Reach {str(objective.get('target_location_id','the destination')).replace('_',' ')}"
    if kind == 'talk':
        return f"Speak with {str(objective.get('target_npc_id','the quest giver')).replace('_',' ')}"
    if kind == 'interact':
        return f"Interact with {count} {str(objective.get('target_item_id','required objects')).replace('_',' ')}"
    return 'Complete the recorded objective'


def quest_terms(quest):
    objectives = '; '.join(objective_text(o) for o in safe_json(quest.get('objectives'),[]))
    items = safe_json(quest.get('item_rewards'),[])
    rewards=[f"{quest.get('gold_reward',0)} gold"]
    for item in items:
        if isinstance(item,dict):
            rewards.append(f"{item.get('quantity',1)} {str(item.get('item_id','item')).replace('_',' ')}")
        elif isinstance(item,str):
            rewards.append(item.replace('_',' '))
    return f"{quest.get('name','Quest')}: {objectives}. Reward: {', '.join(rewards)}."
