# Semantic scene intents — September 13, 2026

The existing typed scene interpreter now handles `list_people`, `observe`, `list_exits`, `inspect_person` and `talk`, alongside environmental actions. Unrecognized questions enter this interpreter instead of receiving a generic location description. Explicit commands and resolvable NPC names/roles retain their direct path.

NPC ID, name, entity type and profession come from the current scene. A requested person reference is matched against living NPCs' names, IDs and authored professions. The engine renders observations, descriptions, exit lists and availability from actual state. It refuses nonexistent IDs and asks for a choice when a role identifies multiple NPCs. Unsupported information requests get a clarification instead of a fabricated answer. Provider failure returns a direct-command suggestion and the current people list.

A semantic observation creates a retry receipt but does not advance time, spend resources, emit action events or call the narrator. The integration test verifies this against real temporary SQLite storage and a repeated request ID.

Active NPC conversation retains its existing meaning: ordinary text addresses that NPC until the player leaves the conversation or gives a recognized action command. This change is not a universal dialogue-versus-command classifier inside active conversations.

## Evidence

- Engine tests cover paraphrase routing, saved-role filters, missing roles, invented IDs, ambiguous captains, authored descriptions, exits without movement, provider failure and direct-command bypass.
- [Initial live diagnostic](local-intent-acceptance-2026-09-13.jsonl): ten Gemma calls. All selected the expected broad operation, but manual inspection caught a missing role filter on the wizard question. The original diagnostic's pass check only checked operation/effect shape and was insufficient for that case.
- The prompt and diagnostic were tightened to retain and check absent roles. [Targeted live retest](local-intent-role-2026-09-13.jsonl) correctly returns no wizard and lists actual alternatives.
- Warm calls in the initial live run took roughly 4.5–5.7 seconds; the targeted retest took 7.1 seconds. Direct commands require no model call. Complex turns that subsequently generate NPC dialogue incur another call.

These are development diagnostics, not held-out accuracy measurements or evidence that every phrasing works. Unit tests stub model proposals to verify engine contracts separately from model quality. No player save was opened by these diagnostics.

Reproduce with a new output filename:

```sh
.venv/bin/python tools/intent_acceptance.py --output /tmp/rpg-intents-new.jsonl
```

Two unrelated normal NPC-scaling tests now explicitly disable the stochastic 2% rare-spawn branch, eliminating a flaky assertion without changing gameplay.
