# Exploration and encounters — September 13, 2026

The reported cave session exposed actual missing mechanics: unlimited search checks, a placeholder discovery, movement-only encounter checks, an accidental ten-turn startup cooldown, and no encounter assigned to Smuggler's Den.

## Current rules

- An area search consumes ten game minutes. Each character gets one attempt per location per character level. Both failure and success are remembered in location properties; reload, rephrasing and leaving/re-entering do not reset the attempt. A level increase permits another attempt. This is a deliberately simple retry rule; tool changes and alternate search approaches do not yet grant separate attempts.
- Search only rolls when explicit authored discoveries remain. No-content and already-completed areas return a concrete no-new-findings response without a fake success roll. Repeating an attempt consumes no time and makes no roll.
- Investigation uses Intelligence; Perception uses Wisdom. Proficiency is checked for the actual skill. The previous unrelated size bonus was removed.
- Successful discoveries persist as IDs and notes, emit concrete discovery events, and are shown again by `look`. The den now has a contraband manifest and signs of an active patrol; Tide Caves has patrol tracks and claw marks. These are clues, not invented item or gold rewards.
- New authored discovery definitions are available to existing saved locations at search time without replacing mutable location state. Historical placeholder discoveries did not record these new IDs, so they do not count as finding the new clues.
- Out-of-combat actions which consume time now check eligible authored encounters in the current location. Random encounters are no longer restricted to movement. Free observations and rejected actions do not add danger exposure. Active-conversation turns still follow their separate existing turn path; they have not been integrated into this danger gate.
- Encounters can occur immediately; missing cooldown history no longer suppresses the first ten turns. If random checks miss, the third eligible exposure in a location selects an authored encounter. Pressure persists across reloads. A ten-turn cooldown follows a spawn, and living hostiles prevent additional spawns in that location. Locations without eligible authored combat encounters do not acquire arbitrary monsters. Puzzle-only entries are excluded from hostile spawning.
- Smuggler's Den now has a single level-one Smuggler Lookout encounter. Contact starts real initiative and combat. The engine does not fabricate a player attack. When the player wins initiative, it prompts for a combat action, including fleeing. If the enemy wins, combatants before the player act and control then returns to the player.
- The status bar includes hours and minutes so time-consuming exploration is visible even when it is still Morning, Day 1.

## Verification

Temporary-save integration tests exercise successful and failed searches, persistence after closing SQLite, repeated requests, new content in old location records, empty-search behavior, encounters on the first search, persistent danger pressure, no encounter from repeated `look`, spawn suppression, and initiative opening without an invented player attack. Enemy-first combat is also checked for a valid player turn afterward. No live player save was modified during development.

This is an encounter and discovery foundation, not a full autonomous world simulation. Other locations need their own authored discoveries to produce useful search results. Existing encounter tables still determine enemy composition and may include dangerous fights; the den's new encounter uses a small level-one opponent. The retry rule and patrol pacing are explicit initial gameplay choices and should be tuned from playtests.
