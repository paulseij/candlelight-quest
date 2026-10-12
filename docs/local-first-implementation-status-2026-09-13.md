# Local-first implementation status — September 13, 2026

The game now uses an authoritative local simulation, native schema-constrained Ollama intent proposals, durable SQLite conversation memory, and committed engine outcomes. The selected default is the official Gemma 4 12B IT QAT Q4_0 model. It remains a provisional choice supported by small local diagnostics, not a universal best-RPG-model claim. See the [research and implementation plan](local-first-implementation-plan-2026-09-10.md) for model comparisons and primary sources.

## Implemented behavior

- Simple commands resolve directly. Freeform interaction produces a typed proposal referencing existing entities, exits, inventory and authored interactable objects. The engine checks permissions and resolves rolls, stock, health, movement and quest rewards. Unsupported interactions cannot create arbitrary items, exits or rewards.
- Environmental operations support opening, locking, rope attachment/traversal, finite item transfer/consumption, damage, revealing authored routes and temporary distraction. Repeating an already-completed operation does not duplicate its effects. The inn shutter is an authored example; these mechanics require explicit content definitions. Descriptive scenery does not automatically become an interactable object.
- Turns resolve against an isolated SQLite copy. State, events, turn count, accepted commitments and retry receipts commit together after checking for concurrent changes. Generation holds no persistent database write lock. Retrying the same request ID returns the original committed result. Invalid actions, observations and no-ops do not advance time; valid failed attempts can.
- Mechanical narration displays the engine's outcome directly. NPC dialogue uses a separate local generation call with current facts, selected verbatim conversation history and engine-backed commitments. Model prose cannot award items or edit world state. Dialogue guards reduce explicit unsupported promises; they are not a general semantic truth checker.
- Both sides of NPC conversations, including greetings, survive reload. SQLite full-text search supplements recent exchanges with relevant older lines. Memory and commitments respect game IDs and timeline ancestry. Embeddings are optional; their absence does not erase conversations or disable exact memory.
- Accepted quest terms use engine-formatted objectives and rewards. Rejected negotiations do not become promises. Commitments are versioned so rewind can restore earlier terms.
- Complete version-2 snapshots restore game-scoped mutable state and fork timeline visibility. Retention settings preserve chosen character progress, companions and relationships. Old incomplete snapshots are rejected explicitly.
- Event indexing runs through a durable outbox and a separate worker connection. Director work is restricted to selected story boundaries; trait evaluation is throttled. Boundary work can still add latency.
- Migration 023 changes authored NPCs, locations, regions, quests and shops to composite `(game_id, id)` keys. A second save no longer takes ownership of the first save's world. Legacy ID-only NPC/shop calls require the active game's scope, or a unique row; ambiguous calls fail instead of editing multiple saves.
- Reviewed training-data preparation is implemented: source provenance, explicit approval, duplicate rejection, and train/validation/test separation by entire scenario family. No model has been fine-tuned, and the MLX training/export path has not been validated.

## Verification and measured performance

As of October 12, the complete test suite passes **1,981 tests**. Coverage includes populated-database migration, two independent saves, transactional rollback and concurrent updates, request retries, timeline memory, accepted quest terms, snapshot restoration, persistent puzzles and scene objects, weather, regional campaigns, tactical combat transitions, authored-object permissions, and a deterministic 100-turn campaign with reloads. Randomized permission tests use synthetic bundles; neither those tests nor the scripted campaign establish long-horizon LLM accuracy.

The real Ollama acceptance test runs the actual turn loop on a temporary save, with embeddings disabled. It checks persistent shutter state, complete conversations after reload, and unchanged gold/inventory after a fabricated reward demand. It does not exercise the full world simulator or establish a hallucination rate.

| Run | Result and timing |
| --- | --- |
| September 13 | Passed. Direct commands 3–7 ms; first model call 15.7 s including 12.2 s loading; subsequent measured NPC replies 4.15, 4.30 and 5.43 s. |
| September 12, later run | Passed state/memory checks, but model calls took 19–52 s. This slowdown remains a material performance caveat. |
| September 12, earlier run | Passed; warm key/reward responses about 3.8–4.0 s. |

Raw evidence: [September 13](local-gameplay-acceptance-2026-09-13.jsonl), [slow September 12 run](local-gameplay-acceptance-final-2026-09-12.jsonl), [earlier September 12 run](local-gameplay-acceptance-2026-09-12.jsonl). A subsequent hardware check observed about 4.5 GB of swap use and a CPU-intensive Python process. That supports investigating contention, but does not establish the cause of the previous slowdown. No unrelated process was stopped. Startup warm-up can reduce the first-turn loading delay; it cannot guarantee latency under contention.

The first September 13 run recalled the desk but omitted that it was locked. A second run explicitly closed and reopened SQLite, rebuilt the engine, and passed the same persistence checks; warm calls took 3.69–4.25 seconds. See the [database-reopen acceptance log](local-gameplay-reopen-2026-09-13.jsonl). That run recalled the locked desk but invented incidental herbs and damp weather in its greeting. Dialogue can still omit details or invent incidental flavor. World effects are constrained by code; conversational truth is not solved by schema validation alone.

## Run and reproduce

```sh
ollama pull hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0
text-rpg check
text-rpg play --new
.venv/bin/python -m pytest -q
.venv/bin/python tools/gameplay_acceptance.py --output /tmp/rpg-acceptance-new.jsonl
```

The acceptance output must be a new file. Gameplay is local with the checked-in configuration. Qwen3.5:9b remains an available comparison/fallback; the earlier diagnostics favored Gemma on these specific grounding fixtures. Avoid keeping multiple large generation models resident on the 18 GB machine.

Prepare an approved corpus, when a measured deficit justifies training:

```sh
.venv/bin/python tools/prepare_rpg_training.py --reviewed-jsonl reviewed.jsonl --output training-splits
```

Each input row requires `family`, `source_ids`, `approved: true`, and chat `messages` ending with the reviewed assistant target. Output includes `train.jsonl`, `valid.jsonl`, `test.jsonl` and a provenance manifest. Generated campaign history is not automatically approved training data.

## Remaining limits

The SQLite copy/compare path is a correctness bridge for existing systems that write during resolution. It copies the whole database and conservatively rejects concurrent state changes, including changes in other saves. Large-save performance needs measurement before replacing it with explicit effect staging. One Database instance is intended for a single active game loop; background indexing opens its own connection.

The migration preserves rows still present in an old database. It cannot recover content already overwritten by the historical cross-save bug. Existing locations are not automatically rewritten with newly authored interactables. Real player saves were not opened or migrated during this implementation; all migration and gameplay verification used temporary databases.

A larger held-out campaign evaluation, broader authored interactions, a controlled MLX runtime comparison, and any conditional fine-tuning remain follow-on work. The research plan should not be read as evidence those experiments have run. The present implementation provides the persistence and mechanics foundation needed to evaluate them meaningfully.
