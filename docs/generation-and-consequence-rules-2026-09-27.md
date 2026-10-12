# Generation and consequential actions — 2026-09-27

## Verified generation

One live request against the configured local model,
`hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0`, generated **The Sulfur Throat**
north of a test copy of The Ash-Silt Outlook. The request took 17.49 seconds:
9.14 seconds loading, 1.74 seconds processing the prompt, and 6.48 seconds
generating 109 tokens. Ollama reported `done=true`, `done_reason=stop`.
The test did not modify a player save. This is a successful smoke test, not
a claim that every possible prompt produces good geography or prose.

Locations now use a bounded native JSON schema. The model supplies a name,
description, terrain type, atmosphere and searchable detail. The engine owns
the ID, saved connections and discovery record. Model output cannot supply IDs
that overwrite existing locations, item grants, NPC records or quest rewards.
There is one compact retry, then a connected fallback. Stored properties mark
whether the location came from the model or the fallback.

The description can still contain mistakes; structural validity does not prove
that every sentence is mechanically meaningful. Further population of a site
must create actual entity, item or encounter records.

## D&D reference and design decision

The official 2024 rules offer different restoration methods with different
requirements:

| Spell | Spell level | Consumed material value | Death window |
| --- | --- | --- | --- |
| Revivify | 3 | 300 gp diamond | 1 minute |
| Raise Dead | 5 | 500 gp diamond | 10 days |
| Reincarnate | 5 | 1,000 gp oils | 10 days |
| Resurrection | 7 | 1,000 gp diamond | 100 years |
| True Resurrection | 9 | 25,000 gp diamonds | 200 years |

These spells do not prescribe a generic success check. The spirit can refuse.
Reanimating a corpse as undead does not restore the former person.
[Official spell descriptions](https://www.dndbeyond.com/sources/dnd/br-2024/spell-descriptions)
and [Dead rule](https://www.dndbeyond.com/sources/dnd/br-2024/rules-glossary#Dead).

The general adjudication policy is: establish whether an approach is possible,
identify the actual power and resources, explain the stakes, then roll only
when the outcome is uncertain. That follows the
[official action and ability-check guidance](https://www.dndbeyond.com/sources/dnd/br-2024/playing-the-game).

For future consequential actions, persist capability requirements, actual
participants, resource stock, time, success/failure outcomes, and retry
conditions. Rephrasing an attempt does not change those facts. Ordinary tasks
need not acquire arbitrary prices; meaningful limits should follow their cause.
A rich, powerful caster can afford more attempts, but cannot buy missing
knowledge, overwrite an unwilling soul, or reuse consumed components.

## Implemented in this change

- `cast revivify on <name>` now restores an eligible present entity to 1 HP.
  Known/prepared spell, class/level, spell slot, recorded death time, corpse and
  soul conditions, and a real diamond are checked. The component and slot are
  consumed atomically with the event and state transition. Invented spells using
  the same effect inherit its minimum spell level and component requirement.
- The appraised diamond has inventory weight/value. Newly seeded Kingsward
  cathedral stock includes one; existing inventories and shops are not reset.
- Six offscreen house-rule rites use existing, living, sufficiently powerful
  authored NPCs. A source needs reserved materials, a charge, physical access
  and a recorded reason to help the target. Both success and failure spend
  supplies. Generation cannot grant this authority. There is a rare daily gate
  and a 30-day background cooldown; supplies never refill merely on reload.
- Revival history is permanent until an explicit timeline rewind. The current
  returned status and consequence remain available to dialogue context even
  after older event summaries fall out of the recent-history window.
- Short rests take one hour; long rests take eight. Another long rest cannot
  start until 16 hours after the previous one finished. Wizard Arcane Recovery
  is available once per long-rest cycle, including when hit dice are exhausted.
  Resting is blocked during combat or in the presence of living hostiles.
  These timings are based on the
  [rest rules](https://www.dndbeyond.com/sources/dnd/br-2024/rules-glossary#LongRest).

## Boundaries

The higher resurrection spells in the reference table are not newly implemented
player spells. Arbitrary player-authored revival rituals, corpse transport,
negotiated supernatural contracts, and mechanical debt-enforcement quests are
still future work. Stored ritual obligations are contextual facts, not complete
quest systems. The offscreen rites are house rules, not copies of the spells.

Invalid Revivify prerequisites are rejected before casting, without spending
resources; this is a deliberate interface safeguard rather than the tabletop
rule for a spell actually cast on an invalid target. Accepted attempts are paid.

The exploration clock is still coarse. Combat death age also uses six seconds
per committed turn, and combat ending now advances one minute instead of ten.
Full second-by-second timing and rest interruptions during elapsed hours are
not implemented. Survival currently advances per action rather than by every
minute of a long rest.

Automated coverage includes model truncation/offline/invalid-schema paths,
return connections, inventory costs, blocked prerequisites, replay protection,
rollback after a failed database write, combat revival, all six offscreen
methods, exhausted/missing sources, and persistent rest/recovery limits.
