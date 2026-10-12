
<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/D%26D-5e%20SRD-cc0000?style=for-the-badge" />
  <img src="https://img.shields.io/badge/LLM-Ollama-7C3AED?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Tests-1981%20passing-brightgreen?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" />
</p>

<p align="center">
<pre align="center">
         .        .            *       .        .    .      .
    .       *            .        .       .     *         .
        .       .    .        .        .            .
  .        *        .    .        .        .    .        *
              .                  /\
   .     *        .    .        /  \       .        .        .
     .        .        .       / .. \          *        .
  .        .     *     .      /  /\  \    .        .        *
        .        .    .      /  /  \  \       .        .
   *       .        .       /  / /\ \  \  .        *       .
     .        .   *   .    /  / /  \ \  \      .        .
  .     *  .        .     /  / /    \ \  \  .       .      *
    .        .        .  /  / /  ()  \ \  \    .        .
        .        .      /  / / /||\ \ \  \       *
   .        *      .   /  / / / || \ \ \  \  .        .
     .    .     .     /  / / /  ||  \ \ \  \ .    *
  .           *      /  / / /   ||   \ \ \  \        .
    .     .     .   /__/_/_/____||____\_\_\__\   .       .
        .       .   |  _  _  _ _||_ _  _  _  |      .
   *        .       | |_||_||_| || |_||_||_| |  .        *
     .        .     |  _  _  _ _||_ _  _  _  |       .
  .    .   *     .  | |_||_||_| || |_||_||_| |  .
    .        .      |  _  _  _ _||_ _  _  _  |     *    .
        .       .   | |_||_||_| || |_||_||_| |  .        .
   .  *     .       |___________||___________|       .
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ .
    ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~
</pre>
</p>

<h1 align="center">Candlelight Quest</h1>

<p align="center">
  <em>A D&D 5e text RPG where dice are law and the narrator is alive.</em>
</p>

<p align="center">
  Every choice ripples. Every roll matters. The LLM narrates your story,<br/>
  but the d20 decides your fate.
</p>

---

## The Idea

Candlelight Quest combines an authoritative D&D 5e simulation with a local LLM for interpreting freeform actions and playing NPCs. The engine resolves dice rolls and world changes, then displays the committed outcome. Generated dialogue adds character voice, while saved conversations and commitments supply continuity.

The result is a game that feels authored and alive, but is mechanically fair.

```
 Morning, Day 3 (08:15)  |  HP [████████░░] 16/20  |  Gold: 47  |  Thornfield Village

 The morning mist curls between the market stalls as merchants begin
 laying out their wares. A blacksmith's hammer rings out from the
 forge at the north end of the square. Old Marta eyes you from behind
 her herbalist's counter, her gaze lingering on the wound across your arm.

 > talk to marta
```

---

## Features

### Core Engine
- **Transactional turn loop**: Interpret → Resolve on isolated state → Validate → Commit → Present
- **Three-ledger SQLite storage**: World State (mutable), Event Ledger (append-only), Canon Ledger (hash-chained)
- **Durable memory**: SQLite conversations, full-text recall and timeline-aware commitments; optional vector retrieval
- **TOML-driven content**: Races, classes, items, spells, regions, factions, quests — all data, no code
- **Pure mechanics**: Every calculation in `mechanics/` is a pure function with zero I/O

### Combat
- **D&D 5e initiative** with full turn-based combat
- **Numbered menu**: `[1] Attack  [2] Cast Spell  [3] Use Item  [4] Flee  [5] Dodge`
- **Readable enemy intent**: Initiative, health, conditions, target and next authored attack are shown before you act
- **NPC AI**: Enemies target vulnerable party members, cycle through their authored moves and may flee when badly wounded
- **Grounded outcomes**: Hit, damage, resistance, wounds, defeat and loot come directly from the rules engine
- **Wounds system**: Heavy hits (>50% HP damage) cause persistent wounds that need healing
- **Death isn't the end**: Lose 25% gold, respawn weakened at the nearest safe location
- **Persistent aftermath**: Finished battles remain part of the location and return you to contextual exploration choices

### World
- **Director system**: Dynamically spawns NPCs, locations, and quests based on pacing and context
- **Plausibility engine**: Try anything — the LLM evaluates plausibility, sets a DC, dice decide
- **Day/night cycle**: 7 time periods affect NPC schedules, encounters, and survival
- **Survival needs**: Hunger, thirst, warmth, fatigue — affected by climate and rest
- **Faction reputation**: 7 tiers from Hated to Honored — affects prices, quests, guard hostility
- **Bounty system**: Crimes accumulate bounties; bounty hunters spawn on roads above 50

### Character
- **23 races** with distinct bonuses, sizes and traits
- **12 classes** with class resources, abilities and spell access
- **Multiclassing**: Up to 2 classes with ability prerequisites
- **Spellcasting**: Full slot system with cantrip scaling, concentration, and arcane recovery
- **14 status conditions**: From Blinded to Unconscious, each with mechanical effects

### Systems

| System | What It Does |
|--------|-------------|
| **Combat** | Turn-based D&D 5e with initiative, attacks, spells, and fleeing |
| **Exploration** | Movement, location discovery, environmental puzzles |
| **Social** | NPC dialogue with mood, affinity tracking, conversation mode |
| **Inventory** | Equipment, categories, sorting, encumbrance |
| **Crafting** | 17 recipes across enchanting, alchemy, smithing, cooking |
| **Shop** | Buy/sell with reputation-adjusted prices and restocking |
| **Companion** | Recruit NPCs (affinity ≥15), gift system, combat allies |
| **Housing** | Buy a home, store items, upgrade with bed/garden/crafting station |
| **Spellcasting** | Spell selection, slot management, upcasting |
| **Rest** | Short/long rest, HP recovery, need restoration, wound healing |
| **Director** | Post-turn AI that spawns content and manages narrative pacing |
| **World Sim** | Weather, NPC schedules, faction events |

### Time Travel
- **Snapshot system**: Auto-saves after long rests, region changes, and every 20 turns
- **Rewind command**: Restore world state while keeping player knowledge
- **Timeline tracking**: Hash-chained canon ledger records divergence points
- **RAG persistence**: ChromaDB memories survive across rewinds — déjà vu is a feature

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Layer                             │
│  status_bar · combat_display · map_display · input_handler   │
├─────────────────────────────────────────────────────────────┤
│                      Engine Layer                            │
│   turn_loop · action_dispatcher · validators · snapshots     │
├──────────────┬──────────────────────────────┬───────────────┤
│  Game Systems │      Director System         │   LLM Layer   │
│  combat      │  triggers · generators       │  provider     │
│  exploration │  schemas · cooldowns         │  context_pack │
│  social      │                              │  output_parse │
│  inventory   │                              │  token_budget │
│  crafting    ├──────────────────────────────┤  11 templates │
│  shop        │      Mechanics Layer          │               │
│  companion   │  dice · combat_math          │               │
│  housing     │  conditions · leveling       │               │
│  rest        │  spellcasting · reputation   │               │
│  spellcast   │  survival · wounds · death   │               │
│  world_sim   │  economy · crafting · affinity│               │
├──────────────┴──────────────────────────────┴───────────────┤
│                      Storage Layer                           │
│   database · versioned migrations · scoped repositories      │
│   Three Ledgers: World State │ Event Ledger │ Canon Ledger   │
├─────────────────────────────────────────────────────────────┤
│                       RAG Layer                              │
│   ChromaDB vector_store · embeddings · retriever · indexer   │
├─────────────────────────────────────────────────────────────┤
│                     Content (TOML)                            │
│   races · classes · items · spells · 19 regions ·            │
│   factions · encounters · quests · shops · stories           │
└─────────────────────────────────────────────────────────────┘
```

### The Turn Loop

Simple commands bypass inference. Freeform proposals reference existing objects and actions; the engine authorizes and resolves their effects.

```text
Player input → Parse or typed local-model proposal
             → Resolve on isolated SQLite state
             → Validate effects and check concurrent changes
             → Commit state + events + receipt atomically
             → Display engine outcome / generate NPC dialogue
             → Background event indexing
```

Director work runs at selected story boundaries. Exact conversation memory works without vector embeddings. Reusing a request ID returns the stored result without repeating effects.

### Input Classification Pipeline

The input handler is the nervous system of the narrator. It decides **what** the player meant:

```
"cast fireball on the goblin"
         │
         ▼
   ┌───────────┐    41 regex patterns checked in order.
   │ Regex Pass │──→ Match found: action=cast_spell, target=fireball,
   └───────────┘                  spell_target=goblin
         │
         │ No match?
         ▼
   ┌───────────┐    LLM classifies with confidence score.
   │ LLM Pass  │──→ action_classify.j2 → OutputParser
   └───────────┘    Threshold: confidence ≥ 0.4
         │
         │ Below threshold?
         ▼
   ┌─────────────┐  Plausibility engine evaluates.
   │ Plausibility │─→ LLM rates 0-100 → logarithmic DC → skill check
   └─────────────┘
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- An LLM provider — one of:
  - A cloud API key (OpenAI or Anthropic), **or**
  - [Ollama](https://ollama.ai) running locally (default: official Gemma 4 12B IT QAT Q4_0)

```bash
# Local option: pull models for narration and RAG embeddings
ollama pull hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0
ollama pull nomic-embed-text
```

RAG memory uses `nomic-embed-text` (Ollama) or `text-embedding-3-small` (OpenAI key).
With an Anthropic-only setup, embeddings fall back to Ollama or an OpenAI key if
available — otherwise the game still runs with RAG memory disabled (a notice is shown).

### Install

```bash
# Clone the repository
git clone https://github.com/Taffesbroodje/candlelight-quest.git
cd candlelight-quest

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"
```

### Play

```bash
# Pick your LLM provider and model (writes config.toml + .env)
text-rpg setup

# Check that your provider and models are available
text-rpg check

# Start a new adventure
text-rpg play --new

# Continue a saved game
text-rpg play --save

# List saved games
text-rpg saves

# Rebuild the GM's RAG memory from the events ledger
# (runs automatically after switching embedding providers)
text-rpg reindex
```

### Configuration

Run `text-rpg setup` for a guided model picker, or edit `config.toml` directly:

```toml
[llm]
provider = "ollama"
model = "hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0"
num_ctx = 8192
think = false

[rag]
# embedding_provider = "openai"  # Optional override: openai | ollama

[game]
starting_region = "verdant_reach"
starting_location = "thornfield_village"

[display]
narrative_width = 80
show_dice_rolls = true      # See the d20 in action
show_mechanics = true       # Show DC checks and modifiers
```

Local Ollama gameplay needs no API key. Optional cloud-provider keys live in `.env`.

See the [local-first implementation status](docs/local-first-implementation-status-2026-09-13.md) for memory, transaction guarantees, measured model latency, migration behavior and remaining limits.

---

## Commands

### Exploration
| Command | Action |
|---------|--------|
| `options` / `what can I do here?` | Show useful actions backed by the current saved scene |
| `go north` / `n` | Move in a direction |
| `look` / `examine chest` | Observe surroundings or a visible feature |
| `search` / `search behind tree` | Search the area or one concrete feature; repeat attempts are bounded |
| `pick up <item>` / `pick up items` | Collect one or all items actually shown on the ground |
| `repair <feature>` | Repair a damaged feature with an appropriate artisan tool and check |
| `solve <approach>` / `answer <response>` / `disarm <method>` / `pick lock` | Attempt a discovered obstacle; failures and completion persist |
| `danger` / `hunt` | Find known combat zones or deliberately seek a hostile encounter here |
| `talk to merchant` | Start a conversation |
| `rest` / `rest long` | Short or long rest |

Plain-language physical actions are also supported. You can sit on a ledge,
hide behind cover, mark or damage an eligible feature, build or clean something,
or leave a mess on the road. The engine resolves the action against visible
objects and persists lasting changes. It does not invent free treasure, routes,
people, or rewards.

### Campaign and continuity
| Command | Action |
|---------|--------|
| `leads` / `campaign` | Show the current regional chapter, routes, contacts, evidence and choices |
| `inspect <record>` | Investigate a listed record at its location |
| `resolve expose` / `resolve secure` | Decide what to do with two independent records at the listed filing place |
| `personal goal` / `personal goal justice` | View or choose a personal goal |
| `history` | Review consequential events, including deaths, revivals and rebuilding |
| `revival options` | See established revival services and recorded dead at your location |
| `revival <person> with <caster>` | Review a ritual's price, time, roll and failure consequences without spending |
| `confirm revival` / `cancel revival` | Accept or decline the current offer; confirmed failures still spend resources |

Every authored region receives a playable two-chapter investigation assembled
from reachable real locations and local contacts. Iron Coast and Ashen Highlands
have bespoke campaigns; other regions use the grounded regional scenario compiler.
Motives and lead order vary by save and remain fixed once introduced. See the
[implementation notes](docs/campaign-continuity-implementation-2026-09-27.md)
for the exact current scope.

### Combat
| Command | Action |
|---------|--------|
| `attack goblin` / `1` | Attack a target |
| `2` | Cast a combat spell |
| `3` | Use a combat item |
| `4` / `flee` | Attempt to flee |
| `5` / `dodge` | Take the Dodge action |
| `grapple <target>` | Contest Athletics to hold a target in place |

Combat panels show initiative order, enemy health, conditions, and each enemy's
next intended move. Creatures with several authored attacks cycle through them;
the displayed intent, damage type, resistance handling, and resolved attack all
refer to the same move. Victory, defeat, and escape return to contextual scene
options, while completed battles leave persistent signs at their location.

### Inventory & Equipment
| Command | Action |
|---------|--------|
| `inventory` / `i` | View inventory |
| `inventory weapons sort value desc` | Filter and sort |
| `equip longsword` | Equip an item |
| `use healing potion` | Use a consumable |
| `craft healing potion` | Craft an item |

### Social
| Command | Action |
|---------|--------|
| `talk to [npc]` | Start dialogue |
| `give potion to healer` | Gift an item to an NPC |
| `recruit wolf` | Recruit a companion (affinity ≥15) |
| `buy longsword` / `sell dagger` | Trade with shops |
| `browse` | View shop inventory |

### Meta
| Command | Action |
|---------|--------|
| `character` / `stats` | View character sheet |
| `spells` / `spellbook` | View known spells |
| `map` | ASCII world map |
| `quests` / `journal` | Quest log |
| `reputation` / `bounty` | Faction standing |
| `rewind` / `undo` | Time travel to last snapshot |
| `home` / `house` | Housing management |
| `save` | Save game |
| `quit` / `q` | Exit |

---

## Testing

The test suite covers 1,981 parametrized tests across the entire codebase:

```bash
# Run all tests
pytest tests/ -v

# Run specific module tests
pytest tests/test_mechanics/ -v        # Pure game math
pytest tests/test_cli/ -v              # Input classification
pytest tests/test_llm/ -v             # LLM output parsing
pytest tests/test_storage/ -v         # Database & repos
pytest tests/test_engine/ -v          # Validators & turn loop
```

### Test Coverage

Tests cover game mechanics, input handling, schema validation, native Ollama requests, transactions, save isolation, timeline memory, snapshot restoration and authored interactions. The full suite passed 1,981 tests on the development machine. Unit tests use local temporary SQLite databases and model stubs; the separate Ollama acceptance script exercises the actual model. See the [verification report](docs/local-first-implementation-status-2026-09-13.md) for measured latency and evaluation limits.

---

## Content

All game content lives in TOML files — add a new race, item, or quest without touching any code:

```
content/
├── classes/          # Fighter, Wizard, Cleric, Rogue
│   └── fighter.toml  # HP dice, proficiencies, features by level
├── races/            # Human, Dwarf, Elf, Halfling, Half-Orc
│   └── dwarf.toml    # Ability bonuses, speed, traits
├── items/            # 6 files: weapons, armor, general, crafting, scrolls, enchanted
├── spells/           # Full spell list with levels, components, effects
├── factions/         # 4 factions with goals, allies, enemies
├── regions/
│   └── verdant_reach/
│       ├── region.toml      # Climate, level range, description
│       ├── locations.toml   # Villages, dungeons, wilderness
│       ├── npcs.toml        # Characters with personalities and schedules
│       ├── encounters.toml  # Combat and puzzle encounters
│       ├── quests.toml      # Quest chains and objectives
│       └── shops.toml       # Merchants and their inventories
└── stories/          # 8 narrative seed files for the Director system
```

### Adding a New Region

Create a new folder under `content/regions/` with the same TOML structure as `verdant_reach/`. The content loader will pick it up automatically.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| LLM | Ollama via LiteLLM |
| Embeddings | nomic-embed-text |
| Vector Store | ChromaDB |
| Database | SQLite (14 migrations) |
| Models | Pydantic v2 |
| Templates | Jinja2 |
| CLI | Typer + Rich |
| Tests | pytest (1,981 tests) |

---

## Project Structure

```
candlelight-quest/
├── config.toml                    # Game configuration
├── pyproject.toml                 # Python package definition
├── src/text_rpg/
│   ├── app.py                     # Main GameApp bootstrap
│   ├── utils.py                   # safe_json, safe_props
│   ├── cli/                       # Terminal UI (Rich + Typer)
│   ├── content/                   # 33 TOML data files
│   ├── engine/                    # Turn loop, dispatcher, validators
│   ├── llm/                       # LLM provider, parser, 11 templates
│   ├── mechanics/                 # 27 pure-function game math modules
│   ├── models/                    # Pydantic data models
│   ├── rag/                       # ChromaDB + embeddings + retrieval
│   ├── storage/                   # SQLite DB, versioned migrations and repositories
│   └── systems/                   # 12 pluggable game systems
└── tests/                         # 1,981 parametrized tests
    ├── conftest.py                # Shared fixtures
    ├── test_mechanics/            # 15 test modules
    ├── test_cli/                  # Input handler tests
    ├── test_llm/                  # Parser + packer + budget tests
    ├── test_engine/               # Validator + turn loop tests
    └── test_storage/              # DB + snapshot tests
```

---

## Design Philosophy

**Dice are law.** The LLM writes prose, not outcomes. A natural 20 is a critical hit whether the narrator likes it or not.

**Content is data.** Adding a new sword means adding 5 lines of TOML, not writing a Python class.

**Pure mechanics.** Every function in `mechanics/` takes data in and returns data out. No database calls, no network requests, no side effects. This is why 280 mechanics tests run in under a second.

**The Director, not the Dungeon Master.** The Director system watches pacing and spawns content when the world needs it — a new NPC when things are quiet, a bounty hunter when tension is high. It doesn't railroad; it populates.

**Memory survives reload.** SQLite stores exact conversations and engine-backed commitments, with save and timeline boundaries. Relevant history is supplied to NPC dialogue; mechanical outcomes come directly from the engine.

---

<p align="center">
<pre align="center">
        )  (
       (   ) )
        ) ( (
       _____)_
    .-'---------|
   ( C|         |
    '-.         |
      '_________|
       /       \
      /   (.)   \
     |           |
      \  .---.  /
       '-------'

   Light the candle.
   Roll the dice.
   Begin your quest.
</pre>
</p>

<p align="center">
  <sub>Built with obsessive attention to game feel by a human and an AI, one dice roll at a time.</sub>
</p>

### Exploring scene features and seeking combat

`look` shows visible scene features and danger notices. `inspect glassy wall`
examines a feature without a roll; `search behind glassy wall` attempts a
persistent, targeted investigation. Each feature tracks its own search attempt:
renaming the command or reloading does not grant another roll at the same level.
Some authored findings reveal collectible catalog items, with existing weights
and prices, exactly once. Not every search yields treasure.

`danger` lists hostile encounter locations in the loaded world. Travel using
normal exits, then use `hunt` to spend ten minutes seeking an encounter. This
respects encounter cooldowns and does not stack new foes on existing hostiles.
Ordinary actions such as `I sing` or `I poop on the road` can resolve without a
check. They spend time; physical traces and the event record persist. They do
not manufacture loot, magical effects, or successful persuasion.

All 421 authored locations compile into three to six persistent scene features.
Features inherit useful actions from what they are: trees can provide cover and
be climbed, signs can be read, water can be waded, and doors can be knocked on.
Inhabited locations receive a modest persistent supporting population who can
discuss supplied public facts but cannot invent quests or rewards. Time of day, NPC schedules,
witness reactions, danger, searches, damage, discoveries, and player traces are
read from saved state when the scene is shown again.

Unlisted actions still pass through the bounded creative adjudicator. The local
model evaluates plausibility against the character, scene, equipment and recent
events; the engine sets the DC, rolls the check, and records the result. Successful
physical work such as digging or arranging can leave a lasting location change.
The engine refuses invented targets, items, routes, creatures and magical effects.
Clear physical commands bypass intent inference, and no second prose call runs
after the roll, keeping the common local-model path responsive.
