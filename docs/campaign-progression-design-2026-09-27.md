# Campaign progression: audit and implementation design

Status: initial playable regional implementation landed after this audit; see
`campaign-continuity-implementation-2026-09-27.md` for exact current scope.
The broader design below remains a roadmap, not a claim that every feature exists.

## What the game actually supports

The game has locations, NPCs, authored encounter tables, quest acceptance,
event-driven objective tracking, rewards, story seeds and a story journal. It
does not yet reliably assemble these into a coherent adventure for a new hero.

Evidence from the current implementation:

- `engine/turn_loop.py:231` calls the Director only following `QUEST_COMPLETE`
  or `COMBAT_END`. Walking, searching and meeting people do not by themselves
  run this story scheduler. This restriction avoids expensive generation on
  ordinary turns, but also suppresses introductions and pacing.
- `systems/social/system.py:123` can prepare a quest when the player explicitly
  asks a hook-bearing NPC for work. There is a path to quests without combat,
  but the player must discover it and find a suitable NPC.
- Of the 15 `content/regions/*/quests.toml` files inspected, 14 contain no
  authored quests; Verdant Reach contains two. This counts those files only,
  not runtime-generated quests or NPC hooks.
- `systems/director/director.py:1119` supports hook/development/escalation/
  resolution story beats and up to two active stories. Earlier generation
  branches can return before these checks run.
- Story activation saves the story before trying to generate its hook quest;
  quest generation can return `None`. A story can therefore exist without
  an actionable opening objective. This is a code-path finding, not a claim
  that a particular player's save has that condition.
- `mechanics/story_seeds.py:155` treats both `hook_quest` and
  `escalation_quest` triggers as any completed quest in the story's flat list.
  Beat-specific dependencies are not reliably represented.
- `systems/director/director.py:1346` applies faction reputation resolution
  effects; other resolution effect types are skipped. Written story promises
  do not necessarily correspond to implemented world changes.
- The `personal.toml` seeds inspected mostly concern NPC personal dramas.
  They are not a guaranteed long-term arc based on the player's chosen origin.
- `engine/quest_tracker.py` and `mechanics/quest_progress.py` provide useful
  authoritative progression, but the current travel/kill/talk/interact
  vocabulary cannot express all the choices described by richer story seeds.

Existing commands: `quests` (also `journal`) shows quests; `stories` shows
story arcs. `look` identifies present NPCs, `talk to <name>` starts conversation,
and asking for work can create a quest if that NPC has a hook. `accept quest`
accepts an available offer. Enemies can appear in authored dangerous locations;
an arbitrary generated scenic location is not guaranteed to contain an
encounter, quest or lead. These are current capabilities, not a satisfactory
onboarding experience.

## Lessons from published D&D adventures

This research uses publicly accessible official guidance, not a claim to have
read every page of the commercial adventures. The designs below are original;
borrow structure rather than importing campaign text or maps.

1. **Lost Mine of Phandelver / Phandalin:** a recurring settlement connects
   NPC relationships, nearby trouble and larger stakes. The official guidance
   explicitly discusses character motivation, local attachments, rumors and
   a place to rest and plan. Apply this by building one substantial starting
   hub with connected adventures before expanding the map.
   [Official Phandalin guidance](https://www.dndbeyond.com/posts/1515-visit-phandalin-d-ds-most-popular-starter-town)
2. **Dragon of Icespire Peak:** the same official guidance identifies its quest
   board as a way to give players direction. Apply a visible opportunities list,
   available through both the journal and appropriate NPCs; it must describe
   actual playable jobs rather than generate enticing rumors without content.
   [Official discussion of the quest board](https://www.dndbeyond.com/posts/1515-visit-phandalin-d-ds-most-popular-starter-town)
3. **Dragons of Stormwreck Isle:** invested NPCs introduce a small choice of
   adventures. Official DM advice recommends warnings about route hazards and
   gives examples of unresolved problems changing the settlement. Apply known
   risks, meaningful approaches, and consequences tied to the underlying problem.
   [Official Stormwreck advice](https://www.dndbeyond.com/posts/1352-dragons-of-stormwreck-isle-tips-for-new-dms)
4. **Curse of Strahd's fortunes:** controlled randomization can vary important
   placements within an established adventure. An official Ravenloft remix
   describes using a tarokka reading to randomize treasures and an ally.
   Apply seeded choices at campaign creation, then persist the result. Do not
   reroll the villain, evidence or explanation when the model loses context.
   [Official Ravenloft remix](https://www.dndbeyond.com/posts/895-strahd-must-die-in-space)

## Recommended campaign structure

Create three connected layers for each save:

- **Regional campaign:** a persistent conflict, opposing factions, named actors,
  secrets, locations, escalation conditions and several possible resolutions.
- **Personal arc:** an origin-related goal and relationship, with opportunities
  to develop or change the goal through play. Use selected backstory facts;
  do not invent a dead relative or traumatic past as established player history.
- **Local life:** independent jobs, social scenes, hazards, trade and discoveries.
  Some connect to the campaign; others have value on their own.

Use authored scenario families with validated variation, rather than asking a
model to invent an entire mechanically viable campaign from scratch. A new
character receives a distinct instance, but variety should come from allies,
motives, placements and decisions rather than incoherent random plot twists.

The first session should establish a goal immediately, present two or three
concrete leads, permit a social or exploration solution as well as combat,
resolve one local problem, and reveal why the larger conflict matters. A player
may refuse the main hook and pursue local life. The journal should still show
what they know and where those leads came from.

Essential clues must survive a failed roll: basic evidence is available through
appropriate interaction; rolls determine extra detail, time, exposure or cost.
Give major revelations multiple independent sources. Defeat, dead NPCs and
declined offers need authored alternatives, not a silent progression dead end.

## Original example: The Broken Beacon

Illustrative proposal using the existing Saltmere setting, not existing content
or a claim about Kami's backstory.

**Opening:** a cargo vessel narrowly avoids wrecking after a false harbor signal.
Harbormaster Kael needs an explanation. Captain Mora knows the vessel's route;
Sal has heard sailors discussing an unusual lantern. The journal identifies
these three concrete leads.

**First adventure:** investigate the watchtower, question a witness, or follow
signs toward the tide caves. Each route can reveal a useful part of the truth.
A lookout wants payment and a safe escape, so talking, sneaking and fighting
are all possible. Finding a shipping record creates a real evidence object
and unlocks a specific lead rather than printing “something interesting.”

**Local resolution:** restore the signal, expose a saboteur, or negotiate a
temporary ceasefire. Each has an explicit reward and a visible harbor change.
Evidence suggests someone is financing the operation inland.

**Personal connection:** connect that financier to one relationship or ambition
the player selected. For a scholar this might be a stolen research collection;
for a former smuggler, an old associate. These are selectable examples, not
assumptions about Kami. The personal goal remains meaningful even if the player
declines employment from the harbor authorities.

**Later chapters:** investigate the sponsor and choose allies, then decide what
to do about the larger operation. Advance chapters through evidence and
decisions; show pressure through patrols, supply trouble and NPC responses.
Never silently reveal the hidden sponsor in a player-facing journal.

## Implementation architecture

Split the current Director into cheap authoritative progression and optional
content preparation. Merely re-enabling all its generation on every turn would
bring back latency without fixing incomplete stories.

1. **Campaign compiler:** instantiate a scenario from a seed, origin and region.
   Bind roles to actual entity IDs; create required sites, evidence and rewards;
   validate prerequisites and save the complete playable opening atomically.
   Persist template version and seed. Reject invalid drafts and use an authored
   fallback. Do not activate an incomplete quest and hope generation repairs it.
2. **Campaign runtime:** evaluate committed events and elapsed game time with
   deterministic rules. Run on creation, relevant travel/discovery/dialogue/
   quest events and timed boundaries, independently of generation cooldowns.
   Reads, invalid commands and model wait time must not advance threat clocks.
3. **Persistent state:** campaign instances, role bindings, beat-specific quest
   IDs, objectives, evidence, known leads, choices, clocks and applied effects.
   Reuse the existing event ledger, transactions and save snapshots; add new
   campaign state to snapshot/restore and timeline branching from the outset.
   Idempotent effect IDs prevent repeated rewards or duplicated actors.
4. **Objective extensions:** evidence acquired, object state changed, NPC rescued,
   agreement recorded and explicit branch choice. Outcomes require engine events;
   model prose cannot complete an objective. Separate return-to-giver conditions
   from generic conversation and make hand-ins consume the required items.
5. **Generation worker:** prepare optional future scenes from a bounded frontier.
   Keep only the next chapter detailed. Validate IDs, reachability, reward budget,
   supported actions and dependencies before publishing content. Freeze revealed
   facts. Discard stale drafts if player decisions change their prerequisites.
   Generation failure must leave an already playable path available.
6. **Journal and scene presentation:** show current goal, why it matters, known
   leads, named contacts, next actionable step, reward and known danger. Surface
   changes when entering a place or resolving a scene. Provide sample commands
   without implying that only those commands are allowed.

Choose encounters from current inhabitants, faction activity, route, noise,
time and unresolved problems. Mix social meetings, signs, hazards and combat.
Give dangerous encounters observable cues and opportunities to avoid or prepare.
Calibrate for a solo low-level character, including action economy and opening
damage; use stronger fixed threats only with clear warning and viable retreat.
Resolve or relocate encounter sources after the player changes the area. Do not
force a fresh hostile spawn simply because an encounter counter fills up.

A richer model or fine-tuning may improve dialogue and interpretation, but cannot
substitute for these state transitions. The existing local model can supply
bounded dialogue and descriptive variation while this architecture carries the
campaign. Measure model upgrades after the playable chapter exists.

## Delivery order and acceptance criteria

1. Repair deterministic story scheduling and beat-specific prerequisites;
   prevent activation without an executable opening.
2. Implement the journal's actionable leads and one complete regional chapter
   using existing locations, NPCs and inventory mechanisms.
3. Add the personal connection, persistent consequences and contextual encounters.
4. Add more scenario families and optional model-assisted chapter preparation.

Target the first chapter at roughly 45–90 minutes as a design goal, then measure
actual play rather than claiming that length from content counts.

Required acceptance runs: finish without combat; finish through combat; fail the
first investigation; decline the first offer; lose a fight; lose a contact; leave
and return; save/reload each beat; repeat a request; rewind and branch; disable
Ollama. Validate no missing targets, duplicate rewards, leaked secrets or
unexplained state changes. A graph validator checks prerequisites and reachable
successors, while end-to-end playthroughs verify that actual commands can execute
the paths. Graph reachability alone does not prove gameplay solvability.

Existing saves should receive an explicit, idempotent campaign introduction bound
to compatible current locations. Preserve possessions, relationships and revealed
facts. No restart should be required merely to introduce the new campaign system.
