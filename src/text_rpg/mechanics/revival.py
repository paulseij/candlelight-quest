"""Requirements for returning an existing person to life.

Named spells follow their requirements, without an extra failure roll. The
offscreen rites are campaign house rules, paid from an existing source's stock.
Animating an undead body is deliberately a different operation.
"""
from dataclasses import dataclass

from text_rpg.utils import safe_props


@dataclass(frozen=True)
class RevivalRite:
    name: str
    min_level: int
    materials_gp: int
    dc: int
    ability: str
    max_age_minutes: int
    consequence: str
    restores_body: bool = False


REVIVAL_RITES = {
    'temple_intercession': RevivalRite('divine intercession', 9, 500, 15, 'wisdom', 14400, 'a debt of service to the temple'),
    'ancestral_call': RevivalRite('an ancestral vigil', 9, 500, 16, 'wisdom', 14400, 'a duty to the ancestors'),
    'fey_bargain': RevivalRite('a fey bargain', 9, 1000, 17, 'charisma', 14400, 'a debt of service to the fey', True),
    'arcane_reconstruction': RevivalRite('arcane reconstruction', 13, 1000, 18, 'intelligence', 52560000, 'an unstable magical aura', True),
    'necromantic_rite': RevivalRite('a soul-retrieval rite', 13, 1000, 18, 'intelligence', 52560000, 'a debt to the soul-retriever', True),
    'wild_magic': RevivalRite('a wild-magic convergence', 13, 1000, 20, 'charisma', 52560000, 'an unstable magical aura', True),
}


def revival_block_reason(target, age_minutes, max_age_minutes, *, restores_body=False):
    if target.get('is_alive', True):
        return 'That person is already alive.'
    props = safe_props(target)
    if age_minutes < 0 or age_minutes > max_age_minutes:
        return 'The death is outside this method’s time limit.'
    if props.get('death_cause') == 'old_age':
        return 'This method cannot reverse death from old age.'
    if props.get('soul_free', True) is False or props.get('soul_willing', True) is False:
        return 'The soul is not free and willing to return.'
    if props.get('creature_type') == 'undead':
        return 'This method cannot restore an undead creature to its former life.'
    if not restores_body and (props.get('body_destroyed') or props.get('missing_vital_parts')):
        return 'This method cannot replace the missing body or vital organs.'
    return None
