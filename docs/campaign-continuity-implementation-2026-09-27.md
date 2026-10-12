# Campaign and continuity implementation (2026-09-27)

This document describes the implemented slice. It does not imply an unlimited
generated campaign or a complete authored world.

## Play now

Restart the program so the code reloads. Continue an existing save. An eligible
save receives its regional introduction once, without removing inventory,
level or discoveries. A regional campaign is also introduced when the player
first enters an eligible region. The `leads` command
shows current goals, exact usable routes, NPC contacts, risk and evidence.
`quests` includes the active chapter. `history` lists consequential events.
`personal goal` lists four goals, and `personal goal <name>` selects one.

At a lead location, use the shown `inspect <record>` command. Two independent
records enable `resolve expose` or `resolve secure` at the listed filing place.
Both choices are executable without an LLM and complete the chapter's quest,
pay its stated reward and carry the consequences into chapter two. The scene
description and `look` show active leads. In the generated Ash-Silt Outlook,
`go back` returns to Highland Road; `leads` shows a route to Cinderhold.

## Persistent state and replayability

- Each region instantiates a grounded two-chapter scenario. Iron Coast and
  Ashen Highlands use bespoke scenarios; the other authored regions compile
  the same campaign structure from their reachable locations and local NPCs. At creation, a
  random seed chooses one of three motives and changes the order of accessible
  clues. Dangerous sites follow safer leads. The seed, motive, chosen sites,
  evidence and decisions are stored in `campaign_instances`; later generation
  cannot change them. This is controlled variation, not an arbitrary plot.
- Quests use the existing objective tracker for campaign evidence and choices.
  Engine events identify the exact quest and objective. Repeated inspections,
  requests and decisions do not duplicate progress or rewards.
- Time creates at most two pressure events per chapter. Completion creates
  two later follow-up notices that cite the earlier decision. These changes
  happen once and remain in history. They currently affect narrative notices
  and faction reputation; they are not a full autonomous faction simulation.
- Four selectable personal goals are tracked against the player's decisions.
  The default uses class as a lightweight starting preference. This is an
  initial personal stake, not a bespoke backstory quest chain.
- Every authored region has a playable scenario. Generated regional variants
  are constrained to existing locations, routes, contacts, risks, and records.

`world_history` records deaths, revivals, campaign evidence, choices, major
consequences, completed quests, and location destruction/rebuilding with cause
IDs. Live entity life state and location condition are authoritative.
Death cannot be reversed by an ordinary entity save or update. A revival
requires a canonical `REVIVAL` event and valid HP in the same transaction.
Rarely (0.8% on an eligible game day, no more than once per 30 game days), a
wandering necromancer can revive a hostile entity with a recorded death at
least three days old. This is an explicit background event: the original
death remains in history, while the NPC's current status becomes alive.

`LOCATION_DESTROYED` persists a destroyed condition. Surviving residents
evacuate to an accessible neighbor and local shops close. The description
changes to ruins, and routine NPC schedules do not move them back. A later
`LOCATION_REBUILT` event restores the current condition; both events remain
visible in `history`. These event types are available for future authored
disasters and reconstruction quests; there is no random town-destruction
generator yet. If the campaign filing site is destroyed, a safe neighboring
site becomes the filing point. If too many independent archives are destroyed,
the chapter explicitly becomes disrupted rather than offering an impossible
objective.

Continuity tables are game scoped and included in snapshots and time-travel
branches. A rewind to an older version-2 snapshot without them clears future
campaign state and history. The event ledger retains its branch semantics.
NPC dialogue receives current life state, location condition and only public
campaign hints; private player evidence is not fed to every NPC. The LLM can
still phrase dialogue poorly, so the authoritative state remains the source
for actions, services, quest progress and the journal.

## Next depth work

The opening chapters establish a dependable playable path and consequences.
World depth still needs more scenario families, distinct social negotiations,
scene objects, tactical encounters linked to factions, personal relationship
arcs, recovery adventures after major disasters, and optional generated future
chapters checked for reachability and state consistency. These must build on
the persistent causal state rather than inventing unrecorded outcomes.
