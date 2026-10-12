"""Answer local people queries from current entities without generation."""
import re

from text_rpg.mechanics.world_clock import get_period
from text_rpg.mechanics.world_sim import is_npc_available

_PEOPLE_QUERY = re.compile(
    r"^(?:who(?:'s| is) (?:out )?(?:here|there|nearby|around)(?: right now)?|"
    r"who (?:can|could) i (?:talk|speak|chat) (?:to|with)|"
    r"(?:is there )?(?:anyone|anybody|someone) (?:here|nearby|around)|"
    r"(?:show|list)(?: me)? (?:the )?(?:people|npcs)(?: (?:here|nearby))?)$", re.I)
_GENERIC_TARGETS = {"", "someone", "somebody", "anyone", "anybody", "a person", "people", "an npc", "a nearby npc"}


def asks_about_people(text: str) -> bool:
    return bool(_PEOPLE_QUERY.fullmatch(text.strip().rstrip('?.!').strip()))


def generic_conversation_target(target: str | None) -> bool:
    return (target or '').strip().lower().rstrip('?.!') in _GENERIC_TARGETS


def nearby_people(context, reference: str | None = None) -> str:
    people = [e for e in context.entities if e.get('entity_type') == 'npc' and e.get('is_alive', True)]
    if reference:
        people = match_people(context, reference)
        if not people:
            return 'Nobody here matches that name or role.\n' + nearby_people(context)
    if not people:
        return 'There is nobody here you can talk to right now. Try another location; `look` shows the exits.'
    period = get_period(context.world_time)
    lines = ['People nearby:']
    available = []
    for npc in people:
        can_talk = is_npc_available(npc, period)
        profession = (npc.get('profession') or '').replace('_', ' ')
        label = f"{npc['name']}" + (f" — {profession}" if profession else '')
        lines.append(f"- {label}" + ('' if can_talk else ' (unavailable right now)'))
        if can_talk:
            available.append(npc)
    if available:
        lines.append(f"Choose someone by name, for example: talk to {available[0]['name']}")
    else:
        lines.append('Nobody here is available to talk right now. Try again later or visit another location.')
    return '\n'.join(lines)


def match_people(context, reference: str) -> list[dict]:
    """Resolve a name, authored ID or profession within the current scene."""
    reference = re.sub(r'^(?:the|a|an)\s+', '', reference.lower().strip())
    people = [e for e in context.entities if e.get('entity_type') == 'npc' and e.get('is_alive', True)]
    if not reference:
        return []
    exact = [e for e in people if reference in {e['id'].lower(), e['name'].lower()}]
    return exact or [e for e in people if reference in e['name'].lower()
                     or reference in (e.get('profession') or '').replace('_', ' ').lower()]


def mentioned_people(context, text: str) -> list[dict]:
    """Keep a model from selecting one of several explicitly matching roles."""
    people = [e for e in context.entities if e.get('entity_type') == 'npc' and e.get('is_alive', True)]
    text = text.lower()
    named = [e for e in people if re.search(r'\b' + re.escape(e['name'].lower()) + r'\b', text)]
    if named:
        return named
    words = set(re.findall(r'\w+', text)) - {'the', 'a', 'an', 'to', 'with', 'i'}
    return [e for e in people if words & (set(re.findall(r'\w+', e['name'].lower())) |
                  set((e.get('profession') or '').replace('_', ' ').lower().split()))]
