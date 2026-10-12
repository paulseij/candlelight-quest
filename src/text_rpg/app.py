"""Main application bootstrap — wires all systems together."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from text_rpg.utils import safe_json

logger = logging.getLogger(__name__)


def _load_config() -> dict[str, Any]:
    """Load config.toml from project root."""
    import tomllib

    config_path = Path(__file__).parent.parent.parent / "config.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    return {}


class GameApp:
    """Main application class that bootstraps and runs the game."""

    def __init__(self, model_override: str | None = None):
        self.config = _load_config()
        self.model_override = model_override

        # Resolved provider (set lazily by llm property)
        self._resolved_provider: str | None = None

        # Why RAG is disabled, if it is (set when embeddings are constructed)
        self.rag_disabled_reason: str | None = None

        # Lazy-initialized components
        self._db = None
        self._llm = None
        self._vector_store = None
        self._embeddings = None
        self._indexer = None
        self._retriever = None
        self._registry = None
        self._dispatcher = None
        self._director = None
        self._turn_loop = None
        self._display = None
        self._input_handler = None
        self._combat_display = None
        self._status_bar = None
        self._map_display = None

        # Current game state
        self.game_id: str | None = None
        self.character: dict | None = None

    # -- Component initialization (lazy) --

    @property
    def db(self):
        if self._db is None:
            from text_rpg.storage.database import Database

            db_path = self.config.get("storage", {}).get("db_path", "saves/game.db")
            self._db = Database(db_path)
            self._db.initialize()
        return self._db

    @property
    def resolved_provider(self) -> str:
        """The active LLM provider, accounting for --model overrides."""
        if self._resolved_provider is None:
            from text_rpg.llm.models import infer_provider

            if self.model_override:
                self._resolved_provider = infer_provider(self.model_override)
            else:
                self._resolved_provider = self.config.get("llm", {}).get("provider", "ollama")
        return self._resolved_provider

    @property
    def llm(self):
        if self._llm is None:
            from text_rpg.llm.env import load_env, get_api_key
            from text_rpg.llm.ollama_provider import DEFAULT_LOCAL_MODEL

            load_env()
            llm_cfg = self.config.get("llm", {})
            model = self.model_override or llm_cfg.get("model", DEFAULT_LOCAL_MODEL)
            provider = self.resolved_provider

            if provider == "ollama":
                from text_rpg.llm.ollama_provider import OllamaProvider

                base_url = llm_cfg.get("base_url", "http://localhost:11434")
                num_ctx = llm_cfg.get("num_ctx", 8192)
                think = llm_cfg.get("think", False)
                # OllamaProvider prepends "ollama/" itself — strip a catalog-style prefix
                model = model.removeprefix("ollama/")
                self._llm = OllamaProvider(model=model, base_url=base_url, num_ctx=num_ctx, think=think)
            else:
                from text_rpg.llm.litellm_provider import LiteLLMProvider

                api_key = get_api_key(provider)
                # For anthropic models, ensure the model ID has the prefix
                if provider == "anthropic" and not model.startswith("anthropic/"):
                    model = f"anthropic/{model}"
                self._llm = LiteLLMProvider(model=model, api_key=api_key)
        self._llm.scene_temperature = self.config.get("llm",{}).get("scene_planner",{}).get("temperature",0.0)
        self._llm.scene_max_tokens = self.config.get("llm",{}).get("scene_planner",{}).get("max_tokens",160)
        return self._llm

    @property
    def vector_store(self):
        if self._vector_store is None:
            from text_rpg.rag.vector_store import VectorStore

            rag_cfg = self.config.get("rag", {})
            prefix = rag_cfg.get("collection_prefix", "text_rpg")
            self._vector_store = VectorStore(
                persist_dir="data/chromadb",
                collection_prefix=prefix,
                embedding_tag=getattr(self.embeddings, "model_tag", ""),
            )
        return self._vector_store

    @property
    def embeddings(self):
        if self._embeddings is None:
            from text_rpg.llm.env import load_env, get_api_key
            from text_rpg.rag.embeddings import LiteLLMEmbeddings, OllamaEmbeddings

            load_env()
            rag_cfg = self.config.get("rag", {})
            provider = self.resolved_provider
            # [rag] embedding_provider overrides; else follow the LLM provider.
            embed_provider = rag_cfg.get("embedding_provider") or provider

            self.rag_disabled_reason = None

            def _ollama():
                return OllamaEmbeddings(
                    model=rag_cfg.get("embedding_model", "nomic-embed-text"),
                    base_url=rag_cfg.get("embedding_base_url", "http://localhost:11434"),
                )

            def _openai():
                return LiteLLMEmbeddings(
                    model="text-embedding-3-small", api_key=get_api_key("openai"),
                )

            if embed_provider == "openai":
                self._embeddings = _openai()
            elif embed_provider == "ollama":
                self._embeddings = _ollama()
            else:
                # Providers without an embedding API (e.g. Anthropic):
                # fall back to Ollama if it's running, else OpenAI-key
                # embeddings, else RAG stays visibly disabled.
                candidate = _ollama()
                if candidate.is_available():
                    self._embeddings = candidate
                elif get_api_key("openai"):
                    self._embeddings = _openai()
                else:
                    self._embeddings = candidate
                    self.rag_disabled_reason = (
                        f"No embedding provider for '{provider}': install Ollama with "
                        "nomic-embed-text, set OPENAI_API_KEY, or set [rag] embedding_provider."
                    )

            if self.rag_disabled_reason is None and not self._embeddings.is_available():
                self.rag_disabled_reason = (
                    "Embedding backend unreachable — semantic search is deferred; saved conversations remain available."
                )
        return self._embeddings

    @property
    def indexer(self):
        if self._indexer is None:
            from text_rpg.rag.indexer import Indexer

            self._indexer = Indexer(self.vector_store, self.embeddings, db=self.db)
        return self._indexer

    @property
    def retriever(self):
        if self._retriever is None:
            from text_rpg.rag.retriever import Retriever

            top_k = self.config.get("rag", {}).get("top_k", 5)
            self._retriever = Retriever(
                self.vector_store, self.embeddings, top_k=top_k, db_path=self.db.db_path
            )
        return self._retriever

    @property
    def registry(self):
        if self._registry is None:
            from text_rpg.engine.system_registry import SystemRegistry

            self._registry = SystemRegistry()
            self._registry.register_defaults()
        return self._registry

    @property
    def director(self):
        if self._director is None:
            from text_rpg.systems.director import Director

            self._director = Director(
                llm=self.llm,
                retriever=self.retriever,
                indexer=self.indexer,
            )
        return self._director

    @property
    def dispatcher(self):
        if self._dispatcher is None:
            from text_rpg.engine.action_dispatcher import ActionDispatcher

            self._dispatcher = ActionDispatcher(self.registry, director=self.director)
        return self._dispatcher

    @property
    def display(self):
        if self._display is None:
            from text_rpg.cli.display import Display

            disp_cfg = self.config.get("display", {})
            self._display = Display(
                width=disp_cfg.get("narrative_width", 80),
                show_dice=disp_cfg.get("show_dice_rolls", True),
                show_mechanics=disp_cfg.get("show_mechanics", True),
            )
        return self._display

    @property
    def input_handler(self):
        if self._input_handler is None:
            from text_rpg.cli.input_handler import InputHandler

            self._input_handler = InputHandler()
        return self._input_handler

    @property
    def combat_display(self):
        if self._combat_display is None:
            from text_rpg.cli.combat_display import CombatDisplay

            self._combat_display = CombatDisplay()
        return self._combat_display

    @property
    def status_bar(self):
        if self._status_bar is None:
            from text_rpg.cli.status_bar import StatusBar

            self._status_bar = StatusBar(self.display.console)
        return self._status_bar

    @property
    def map_display(self):
        if self._map_display is None:
            from text_rpg.cli.map_display import MapDisplay

            self._map_display = MapDisplay(self.display.console)
        return self._map_display

    # -- Repos (convenience accessors) --

    def _get_repos(self) -> dict[str, Any]:
        self.db.active_game_id = self.game_id
        from text_rpg.storage.repos.save_game_repo import SaveGameRepo
        from text_rpg.storage.repos.character_repo import CharacterRepo
        from text_rpg.storage.repos.entity_repo import EntityRepo
        from text_rpg.storage.repos.location_repo import LocationRepo
        from text_rpg.storage.repos.event_ledger import EventLedgerRepo
        from text_rpg.storage.repos.world_state_repo import WorldStateRepo
        from text_rpg.storage.repos.intent_repo import IntentRepo
        from text_rpg.storage.repos.trade_skill_repo import TradeSkillRepo
        from text_rpg.storage.repos.spell_repo import SpellRepo
        from text_rpg.storage.repos.reputation_repo import ReputationRepo
        from text_rpg.storage.repos.shop_repo import ShopRepo
        from text_rpg.storage.repos.companion_repo import CompanionRepo
        from text_rpg.storage.repos.housing_repo import HousingRepo
        from text_rpg.storage.repos.connection_repo import ConnectionRepo
        from text_rpg.storage.repos.snapshot_repo import SnapshotRepo
        from text_rpg.storage.repos.trait_repo import TraitRepo
        from text_rpg.storage.repos.spell_creation_repo import SpellCreationRepo
        from text_rpg.storage.repos.guild_repo import GuildRepo
        from text_rpg.storage.repos.custom_item_repo import CustomItemRepo

        return {
            "save_game": SaveGameRepo(self.db),
            "character": CharacterRepo(self.db),
            "entity": EntityRepo(self.db),
            "location": LocationRepo(self.db),
            "event_ledger": EventLedgerRepo(self.db),
            "world_state": WorldStateRepo(self.db),
            "intent": IntentRepo(self.db),
            "trade_skill": TradeSkillRepo(self.db),
            "spell": SpellRepo(self.db),
            "reputation": ReputationRepo(self.db),
            "shop": ShopRepo(self.db),
            "companion": CompanionRepo(self.db),
            "housing": HousingRepo(self.db),
            "connection": ConnectionRepo(self.db),
            "snapshot": SnapshotRepo(self.db),
            "trait": TraitRepo(self.db),
            "spell_creation": SpellCreationRepo(self.db),
            "guild": GuildRepo(self.db),
            "custom_item": CustomItemRepo(self.db),
        }

    # -- Public interface --

    def main_menu(self) -> None:
        """Show main menu and handle selection."""
        self.display.show_title_screen()

        while True:
            choice = self.display.show_main_menu()
            if choice == "1":
                self.new_game()
                break
            elif choice == "2":
                if self._try_continue():
                    break
            elif choice == "3":
                self.list_saves()
                save_name = self.display.get_input("Enter save name > ")
                if save_name:
                    self.load_game(save_name)
                    break
            elif choice == "4":
                self.display.show_system_check()
            elif choice == "5":
                self.display.show_info("Farewell, adventurer!")
                return
            else:
                self.display.show_error("Invalid choice.")

    def new_game(self) -> None:
        """Start a new game with character creation."""
        from text_rpg.cli.character_creator import CharacterCreator
        from text_rpg.content.loader import load_all_races, load_all_classes, load_all_origins
        from text_rpg.mechanics.character_creation import create_character

        # Character creation — origins are loaded and passed into the creator
        races = load_all_races()
        classes = load_all_classes()
        all_origins = load_all_origins()
        creator = CharacterCreator()
        params = creator.run(races, classes, origins=all_origins)

        # Extract origin from character creator result
        chosen_origin = params.get("origin")

        # Create game
        self.game_id = str(uuid.uuid4())
        game_cfg = self.config.get("game", {})
        if chosen_origin:
            starting_region = chosen_origin.get("starting_region", game_cfg.get("starting_region", "verdant_reach"))
            starting_location = chosen_origin.get("starting_location", "thornfield_village")
        else:
            starting_region = game_cfg.get("starting_region", "verdant_reach")
            starting_location = game_cfg.get("starting_location", "thornfield_village")

        # Build character — include origin bonuses
        cls_data = classes.get(params["char_class"], {})
        starting_gold = cls_data.get("starting_gold", 0)
        if chosen_origin:
            starting_gold += chosen_origin.get("bonus_gold", 0)

        # Merge origin skills with class skill choices (deduplicated)
        skill_choices = list(params["skill_choices"])
        if chosen_origin:
            for s in chosen_origin.get("skill_proficiencies", []):
                if s not in skill_choices:
                    skill_choices.append(s)

        char_dict = create_character(
            name=params["name"],
            race=params["race"],
            char_class=params["char_class"],
            ability_scores=params["ability_scores"],
            skill_choices=skill_choices,
            game_id=self.game_id,
            starting_gold=starting_gold,
            origin_id=chosen_origin["id"] if chosen_origin else None,
            origin_primary=params.get("origin_primary"),
            origin_secondary=params.get("origin_secondary"),
        )
        self.character = char_dict

        # Initialize database
        repos = self._get_repos()
        repos["save_game"].create_game(
            self.game_id, params["name"], char_dict["id"], starting_location
        )
        repos["character"].save(char_dict)

        # Seed the starting region (locations, connections, NPCs, quests, shops)
        from text_rpg.content.region_seeder import seed_region

        seed_region(
            starting_region, self.game_id, repos,
            visited_location_id=starting_location,
        )

        # Create inventory
        from text_rpg.content.loader import load_all_items

        items = load_all_items()
        starting_items = []
        # Give starter equipment based on class
        starting_eq = cls_data.get("starting_equipment", {})
        for weapon_id in starting_eq.get("weapons", []):
            if weapon_id in items:
                starting_items.append({"item_id": weapon_id, "quantity": 1})
        for armor_id in starting_eq.get("armor", []):
            if armor_id in items:
                starting_items.append({"item_id": armor_id, "quantity": 1})
        for item_id in starting_eq.get("items", []):
            if item_id in items:
                starting_items.append({"item_id": item_id, "quantity": 1})
        # Add origin starting equipment (deduplicated against class items)
        if chosen_origin:
            existing_ids = {si["item_id"] for si in starting_items}
            for item_id in chosen_origin.get("starting_equipment", []):
                if item_id in items and item_id not in existing_ids:
                    starting_items.append({"item_id": item_id, "quantity": 1})
                    existing_ids.add(item_id)

        # Everyone gets a healing potion
        starting_items.append({"item_id": "healing_potion", "quantity": 2})

        inv_id = str(uuid.uuid4())
        inv_dict = {
            "id": inv_id,
            "game_id": self.game_id,
            "owner_id": char_dict["id"],
            "items": json.dumps(starting_items),
        }
        repos["world_state"].save_inventory(inv_dict)

        # Auto-equip first weapon and first armor from starting equipment
        first_weapon = next((w for w in starting_eq.get("weapons", []) if w in items), None)
        first_armor = next(
            (a for a in starting_eq.get("armor", [])
             if a in items and items[a].get("armor_type") != "shield"),
            None,
        )
        if first_weapon:
            char_dict["equipped_weapon_id"] = first_weapon
        if first_armor:
            char_dict["equipped_armor_id"] = first_armor
            # Recalculate AC based on equipped armor
            from text_rpg.mechanics.combat_math import calculate_ac
            armor_data = items[first_armor]
            dex_mod = (char_dict["ability_scores"].get("dexterity", 10) - 10) // 2
            char_dict["ac"] = calculate_ac(
                armor_data.get("ac_base", 10), dex_mod,
                armor_data.get("armor_type", "light"),
            )
        # Check for shield separately (equip alongside armor)
        shield_id = next(
            (a for a in starting_eq.get("armor", [])
             if a in items and items[a].get("armor_type") == "shield"),
            None,
        )
        if shield_id and first_armor:
            # Shield adds +2 to existing armor AC
            char_dict["ac"] = char_dict.get("ac", 10) + 2
        elif shield_id and not first_armor:
            char_dict["equipped_armor_id"] = shield_id
            dex_mod = (char_dict["ability_scores"].get("dexterity", 10) - 10) // 2
            char_dict["ac"] = 10 + dex_mod + 2

        # Save character with equipped gear and recalculated AC
        repos["character"].save(char_dict)
        self.character = char_dict

        # Initialize faction reputations from TOML defaults + origin adjustments
        from text_rpg.content.loader import load_all_factions
        factions = load_all_factions()
        origin_faction_reps = {}
        if chosen_origin:
            origin_faction_reps = chosen_origin.get("faction_reputation", {})
        for faction_id, faction_data in factions.items():
            default_rep = faction_data.get("default_reputation", 0)
            origin_rep = origin_faction_reps.get(faction_id, 0)
            repos["reputation"].set_faction_rep(self.game_id, faction_id, default_rep + origin_rep)

        # Learn starting spells for spellcasters
        self._learn_starting_spells(char_dict, repos)

        # Seed RAG if available
        self._seed_rag()

        # Log game start event
        origin_name = chosen_origin["name"] if chosen_origin else "a wandering traveler"
        repos["event_ledger"].append({
            "id": str(uuid.uuid4()),
            "game_id": self.game_id,
            "event_type": "WORLD_CHANGE",
            "turn_number": 0,
            "timestamp": self._now(),
            "actor_id": char_dict["id"],
            "target_id": None,
            "location_id": starting_location,
            "description": f"{params['name']} begins their adventure as {origin_name}.",
            "mechanical_details": json.dumps({"origin": chosen_origin["id"] if chosen_origin else "wandering_traveler"}),
            "is_canonical": True,
        })

        # Show prologue
        self.display.show_success(f"\n{params['name']} has been created!")
        if chosen_origin and chosen_origin.get("prologue"):
            self.display.show_prologue(chosen_origin["prologue"])

        # Show how to play
        self.display.show_how_to_play()

        self._run_game_loop()

    def load_game(self, save_name: str) -> None:
        """Load a saved game."""
        repos = self._get_repos()
        games = repos["save_game"].list_games()
        game = None
        for g in games:
            if g["name"] == save_name:
                game = g
                break
        if not game:
            self.display.show_error(f"Save '{save_name}' not found.")
            return

        self.game_id = game["id"]
        self.character = repos["character"].get_by_game(self.game_id)
        if not self.character:
            self.display.show_error("Save is corrupted — no character found.")
            return

        self.display.show_success(f"Loaded: {self.character['name']}")
        self._run_game_loop()

    def list_saves(self) -> None:
        """List saved games."""
        repos = self._get_repos()
        games = repos["save_game"].list_games()
        saves = []
        for g in games:
            char = repos["character"].get_by_game(g["id"])
            saves.append({
                "name": g["name"],
                "character_name": char["name"] if char else "Unknown",
                "turn_number": g.get("turn_number", 0),
                "created_at": g.get("created_at", ""),
            })
        self.display.show_saves_list(saves)

    # -- Game loop --

    def _run_game_loop(self) -> None:
        """Main game loop."""
        repos = self._get_repos()
        game = repos["save_game"].get_game(self.game_id)
        if not game:
            self.display.show_error("Game not found.")
            return

        current_location_id = game.get("current_location_id")
        location = repos["location"].get(current_location_id, self.game_id)
        if not location:
            self.display.show_error(f"Location '{current_location_id}' not found.")
            return

        # Build context packer and turn loop
        from text_rpg.llm.context_packer import ContextPacker
        from text_rpg.engine.turn_loop import TurnLoop

        from text_rpg.llm.token_budget import TokenBudget
        context_packer = ContextPacker(TokenBudget(max_context_tokens=1200))

        # Inject dependencies into all registered systems
        self.dispatcher.repos = repos
        self.registry.inject_all(director=self.director, repos=repos, llm=self.llm)

        from text_rpg.systems.world_sim import WorldSimulator
        from text_rpg.engine.event_handlers import PostTurnEventHandler
        world_sim = WorldSimulator(repos)
        event_handler = PostTurnEventHandler(self.game_id, repos, self.display)

        turn_loop = TurnLoop(
            registry=self.registry,
            dispatcher=self.dispatcher,
            llm_provider=self.llm,
            context_packer=context_packer,
            retriever=self.retriever,
            indexer=self.indexer,
            repos=repos,
            director=self.director,
            world_sim=world_sim,
        )

        from text_rpg.engine.campaign import CampaignRuntime
        campaign = CampaignRuntime(repos)
        if campaign.ensure(self.game_id):
            self.display.show_info(campaign.get(self.game_id)['template']['opening'])
        if campaign.get(self.game_id):
            self.display.show_info('Use leads for campaign objectives, routes, and evidence; history recalls major events.')
        self._show_location(location, repos)

        from text_rpg.llm.ollama_provider import OllamaProvider
        if isinstance(self.llm,OllamaProvider):
            import threading
            threading.Thread(target=self.llm.warmup,name="rpg-model-warmup",daemon=True).start()

        autosave_interval = self.config.get("game", {}).get("autosave_interval", 5)
        narrator_cfg = self.config.get("llm",{}).get("narrator",{})
        turn_loop.dialogue_temperature = narrator_cfg.get("temperature",0.7)
        turn_loop.dialogue_max_tokens = narrator_cfg.get("max_tokens",200)
        turn_number = game.get("turn_number", 0)

        # RAG health: warn when disabled, rebuild vectors after a provider switch
        _ = self.embeddings  # ensure rag_disabled_reason has been evaluated
        if self.rag_disabled_reason:
            self.display.show_info(f"Note: {self.rag_disabled_reason}")
        else:
            self._maybe_reindex_rag(repos)

        while True:
            # Refresh character for status bar
            self.character = repos["character"].get_by_game(self.game_id)

            # Render status bar before prompt
            cur_game = repos["save_game"].get_game(self.game_id)
            wt = cur_game.get("world_time", 480) if cur_game else 480
            cur_loc_id = cur_game.get("current_location_id", "") if cur_game else ""
            cur_loc = repos["location"].get(cur_loc_id, self.game_id) if cur_loc_id else None
            loc_name = cur_loc.get("name", "") if cur_loc else ""

            from text_rpg.mechanics import world_clock
            world_time_info = {
                "time_of_day": world_clock.get_period(wt),
                "day": world_clock.get_day(wt),
                "clock": f"{world_clock.get_hour(wt):02d}:{world_clock.get_minute(wt):02d}",
            }
            self.status_bar.render(self.character, loc_name, world_time_info)

            # Check for active combat → show combat menu
            active_combat = repos["world_state"].get_active_combat(self.game_id)
            if active_combat and active_combat.get("is_active"):
                enemies = [c for c in active_combat.get("combatants", [])
                          if c.get("combatant_type") == "enemy" and c.get("is_active", True)]
                self.combat_display.show_combat_menu(active_combat, self.character, enemies)
                prompt = "\n[Combat] > "
            else:
                # Show conversation indicator in prompt
                conv = turn_loop.active_conversation
                if conv:
                    prompt = f"\n[Speaking with {conv['npc_name']}] > "
                else:
                    prompt = "\n> "
            raw_input = self.display.get_input(prompt)
            if not raw_input:
                continue

            # Handle meta commands locally (work even in conversation)
            classified = self.input_handler.classify(raw_input)
            if raw_input.strip().lower() in {'campaign', 'campaign journal'}:
                self.display.show_info(campaign.journal(self.game_id))
                continue
            if classified.get("is_meta"):
                meta_result = self._handle_meta(classified, repos, turn_number, turn_loop)
                if meta_result == "break":
                    break
                if meta_result == "continue":
                    continue

            # Confirm before attacking non-hostile NPCs
            if classified.get("action_type") == "attack" and not (active_combat and active_combat.get("is_active")):
                if self._should_confirm_attack(classified.get("target", ""), repos):
                    confirm = self.display.get_input("Are you sure? This will have consequences. (y/n) > ")
                    if confirm.lower() not in ("y", "yes"):
                        self.display.show_info("You stay your hand.")
                        continue

            # Process game turn
            try:
                with self.display.console.status("Processing your turn…", spinner="dots") as status:
                    turn_loop.progress_callback = status.update
                    try:
                        result = turn_loop.process_turn(raw_input, self.game_id)
                    finally:
                        turn_loop.progress_callback = None
            except Exception as e:
                logger.exception("Turn processing error")
                self.display.show_error(f"Something went wrong: {e}")
                continue

            turn_number = repos["save_game"].get_game(self.game_id)["turn_number"]

            # Show dice rolls (DiceRoll dataclasses, or dicts for safety)
            if result.action_result and hasattr(result.action_result, "dice_rolls"):
                for dr in result.action_result.dice_rolls:
                    data = dr if isinstance(dr, dict) else vars(dr)
                    self.display.show_dice_roll(
                        data.get("dice_expression", ""),
                        data.get("rolls", []),
                        data.get("modifier", 0),
                        data.get("total", 0),
                        data.get("purpose", ""),
                    )

            # Show mechanical summary
            if result.mechanical_summary and not (self.display.show_dice and result.action_result and result.action_result.dice_rolls):
                self.display.show_mechanical_summary(result.mechanical_summary)

            # Show narrative
            if result.narrative:
                self.display.show_narrative(result.narrative)

            # One-time warning when narration degraded because the LLM is unreachable
            if getattr(result, "llm_degraded", False) and not getattr(self, "_llm_warned", False):
                self._llm_warned = True
                self.display.show_error(
                    "The storyteller (LLM provider) is unreachable — using plain mechanical text. "
                    "Check your connection or API key, or run 'text-rpg setup'."
                )

            # Process post-turn events (quests, reputation, bounties)
            event_handler.process(result)

            # Show level-up notification
            if result.level_up:
                self.display.show_level_up(
                    result.level_up["new_level"],
                    result.level_up["hp_gained"],
                    result.level_up.get("new_features"),
                )

            # Show survival need warnings
            if result.needs_warnings:
                for warning in result.needs_warnings:
                    self.display.console.print(f"  [dark_orange]{warning}[/dark_orange]")

            # Update character state
            self.character = repos["character"].get_by_game(self.game_id)

            # Check if location changed — also ends any active conversation
            game = repos["save_game"].get_game(self.game_id)
            new_loc_id = game.get("current_location_id")
            if new_loc_id != current_location_id:
                current_location_id = new_loc_id
                turn_loop.end_conversation()
                location = repos["location"].get(current_location_id, self.game_id)
                if location:
                    self._show_location(location, repos)

            # Autosave
            if turn_number % autosave_interval == 0:
                self._save_game(repos, turn_number)

    # -- Helpers --

    def _handle_meta(self, classified: dict, repos: dict, turn_number: int, turn_loop: Any) -> str | None:
        """Handle a meta command. Returns 'break', 'continue', or None."""
        action_type = classified["action_type"]

        if action_type == "quit":
            self._save_game(repos, turn_number)
            self.display.show_info("Game saved. Farewell!")
            return "break"

        if action_type == "save":
            self._save_game(repos, turn_number)
            self.display.show_success("Game saved!")
            return "continue"

        if action_type == "help":
            active_c = repos["world_state"].get_active_combat(self.game_id)
            if active_c and active_c.get("is_active"):
                help_mode = "combat"
            elif turn_loop.active_conversation:
                help_mode = "conversation"
            else:
                help_mode = "exploration"
            self.display.show_help(help_mode)
            return "continue"

        if action_type == "inventory":
            inv_params = classified.get("parameters", {})
            self._show_inventory(
                repos,
                category_filter=inv_params.get("category"),
                sort_by=inv_params.get("sort_by", "name"),
                sort_desc=inv_params.get("sort_desc", False),
            )
            return "continue"

        if action_type == "rewind":
            self._handle_rewind(repos, turn_loop)
            return "continue"

        # Simple meta commands that call a single method
        _simple = {
            "character": lambda: self._show_character_sheet(repos),
            "skills": lambda: self.display.show_skills(self.character),
            "quests": lambda: self.display.show_quest_log(repos["world_state"].get_all_quests(self.game_id)),
            "spells": lambda: self._show_spells(repos),
            "recipes": lambda: self._show_trade_skills(repos),
            "reputation": lambda: self._show_reputation(repos),
            "bounty": lambda: self._show_bounty(repos),
            "stories": lambda: self._show_stories(repos),
            "map": lambda: self._show_map(repos),
            "traits": lambda: self._show_traits(repos),
            "combinations": lambda: self._show_combinations(repos),
            "guild_info": lambda: self._show_guild_info(repos),
            "job_board": lambda: self._show_job_board(repos),
        }
        handler = _simple.get(action_type)
        if handler:
            handler()
            return "continue"

        return None

    def _show_location(self, location: dict, repos: dict) -> None:
        """Display a location with its entities and items."""
        connections = safe_json(location.get("connections"), [])
        if repos.get("connection"):
            connections = repos["connection"].get_connections(self.game_id, location["id"])

        entities_ids = safe_json(location.get("entities"), [])

        # Get entity names from the location (filtered by schedule availability)
        entity_names = []
        loc_entities = repos["entity"].get_by_location(self.game_id, location["id"])

        # Get current time period for NPC availability
        game = repos["save_game"].get_game(self.game_id)
        wt = game.get("world_time", 480) if game else 480

        from text_rpg.mechanics import world_clock
        from text_rpg.mechanics.world_sim import is_npc_available
        period = world_clock.get_period(wt)

        for e in loc_entities:
            if e.get("is_alive", True):
                if e.get("entity_type") == "npc" and not is_npc_available(e, period):
                    continue  # NPC is sleeping/unavailable
                entity_names.append(e["name"])

        from text_rpg.systems.inventory.pickup import ground_entries
        from text_rpg.systems.item_catalog import get_item_catalog
        catalog = get_item_catalog(repos, self.game_id, (self.character or {}).get("id"))
        items_ids = [f"{entry['name']} (x{entry['quantity']})"
                     for entry in ground_entries(location.get("items"), catalog)]

        # Build location name with time
        time_str = world_clock.format_short(wt)
        display_name = f"{location['name']} — {time_str}"
        from text_rpg.engine.continuity import Continuity
        condition = Continuity(repos).location_condition(self.game_id, location['id'])
        description = location.get('description', '')
        if condition and condition['condition'] == 'destroyed':
            description = 'This place was destroyed. Ruins and debris remain; former services and ordinary daily activity have stopped.'
        elif condition and condition['condition'] == 'rebuilt':
            description = 'This place has been rebuilt since its destruction. ' + description

        from text_rpg.engine.scene_actions import SceneActions
        from text_rpg.engine.scene_detail import scene_summary
        from text_rpg.systems.base import GameContext
        scene_location = dict(location, description=description)
        scene_context = GameContext(self.game_id, self.character or {}, scene_location, loc_entities)
        summary = scene_summary(scene_location, SceneActions(repos, None).objects(scene_context), loc_entities,
                                period, self.game_id, wt)
        if summary:
            description += '\n\n' + summary

        self.display.show_location(
            name=display_name,
            description=description + self._campaign_scene(repos, location['id']),
            exits=connections,
            entities=entity_names if entity_names else None,
            items=items_ids if items_ids else None,
        )

    def _campaign_scene(self, repos, location_id):
        from text_rpg.engine.campaign import CampaignRuntime
        text = CampaignRuntime(repos).scene(self.game_id, location_id)
        return '\n\n' + text if text else ''

    def _show_inventory(
        self, repos: dict, category_filter: str | None = None,
        sort_by: str = "name", sort_desc: bool = False,
    ) -> None:
        """Show player inventory with gold, equipped markers, and carry weight."""
        if not self.character:
            return
        inv = repos["world_state"].get_inventory(self.character["id"], self.game_id)
        if not inv:
            self.display.show_info("Your pack is empty.")
            return

        items_data = safe_json(inv.get("items"), [])

        from text_rpg.mechanics.ability_scores import modifier
        from text_rpg.systems.item_catalog import get_item_catalog

        all_items = get_item_catalog(repos, self.game_id, self.character.get("id"))
        gold = self.character.get("gold", 0)

        # Calculate carry weight and max
        total_weight = 0.0
        for entry in items_data:
            item_id = entry.get("item_id", "")
            qty = entry.get("quantity", 1)
            item = all_items.get(item_id, {})
            total_weight += item.get("weight", 0.0) * qty

        scores = safe_json(self.character.get("ability_scores"), {})
        str_score = scores.get("strength", 10)
        max_carry = str_score * 15  # D&D 5e carrying capacity

        self.display.show_inventory(
            items=items_data,
            all_items_data=all_items,
            gold=gold,
            equipped_weapon_id=self.character.get("equipped_weapon_id"),
            equipped_armor_id=self.character.get("equipped_armor_id"),
            category_filter=category_filter,
            sort_by=sort_by,
            sort_desc=sort_desc,
            carry_weight=total_weight,
            max_carry_weight=float(max_carry),
        )

    def _show_character_sheet(self, repos: dict) -> None:
        """Show character sheet with equipped item names."""
        if not self.character:
            return

        from text_rpg.systems.item_catalog import get_item_catalog
        all_items = get_item_catalog(repos, self.game_id, self.character.get("id"))

        equipped_items = {}
        weapon_id = self.character.get("equipped_weapon_id")
        armor_id = self.character.get("equipped_armor_id")
        if weapon_id:
            weapon_data = all_items.get(weapon_id, {})
            equipped_items["weapon_name"] = weapon_data.get("name", weapon_id)
            equipped_items["weapon_dice"] = weapon_data.get("damage_dice", "")
        else:
            equipped_items["weapon_name"] = "Unarmed"
        if armor_id:
            armor_data = all_items.get(armor_id, {})
            equipped_items["armor_name"] = armor_data.get("name", armor_id)
        else:
            equipped_items["armor_name"] = "None"

        # Get active bounties for character sheet
        bounties = []
        rep_repo = repos.get("reputation")
        if rep_repo:
            bounties = rep_repo.get_all_bounties(self.game_id)

        self.display.show_character_sheet(self.character, equipped_items, bounties=bounties)

    def _show_trade_skills(self, repos: dict) -> None:
        """Show trade skills and available recipes."""
        if not self.character:
            return
        self.display.show_trade_skills(
            repos["trade_skill"].get_skills(self.game_id, self.character["id"]),
            repos["trade_skill"].get_known_recipes(self.game_id, self.character["id"]),
        )

    def _should_confirm_attack(self, target_name: str, repos: dict) -> bool:
        """Check if attacking this target should require confirmation (non-hostile NPC)."""
        if not target_name or not self.game_id:
            return False
        game = repos["save_game"].get_game(self.game_id)
        loc_id = game.get("current_location_id", "") if game else ""
        if not loc_id:
            return False
        entities = repos["entity"].get_by_location(self.game_id, loc_id)
        target_lower = target_name.lower()
        for e in entities:
            if not e.get("is_alive", True):
                continue
            name_lower = e["name"].lower()
            if name_lower == target_lower or target_lower in name_lower or name_lower in target_lower:
                return not e.get("is_hostile", False)
        return False

    def _show_reputation(self, repos: dict) -> None:
        """Show faction reputation standings."""
        from text_rpg.content.loader import load_all_factions
        factions_data = load_all_factions()
        faction_reps = repos["reputation"].get_all_faction_reps(self.game_id)
        self.display.show_reputation(faction_reps, factions_data)

    def _show_stories(self, repos: dict) -> None:
        """Show story journal — active and completed arcs."""
        from text_rpg.content.loader import load_all_story_seeds

        all_seeds_list = load_all_story_seeds()
        all_seeds = {s["id"]: s for s in all_seeds_list}
        active = repos["world_state"].get_active_stories(self.game_id)
        completed = repos["world_state"].get_completed_story_ids(self.game_id)
        self.display.show_journal(active, completed, all_seeds)

    def _show_map(self, repos: dict) -> None:
        """Show ASCII map of visited locations."""
        game = repos["save_game"].get_game(self.game_id)
        current_loc_id = game.get("current_location_id", "") if game else ""
        all_locs = repos["location"].get_all(self.game_id)
        # Get home location
        home_loc_id = None
        if repos.get("housing") and self.character:
            home = repos["housing"].get_home(self.game_id, self.character.get("id", ""))
            if home:
                home_loc_id = home.get("location_id")
        conn_repo = repos.get("connection")
        self.map_display.render(
            all_locs, current_loc_id, home_loc_id,
            connection_repo=conn_repo,
            game_id=self.game_id,
            total_locations=len(all_locs),
        )

    def _show_traits(self, repos: dict) -> None:
        """Show acquired dynamic traits."""
        from text_rpg.mechanics.trait_effects import format_effect_description

        trait_repo = repos.get("trait")
        if not trait_repo or not self.character:
            self.display.show_info("No traits acquired yet.")
            return

        traits = trait_repo.get_traits(self.game_id, self.character.get("id", ""))
        if not traits:
            self.display.show_info("No traits acquired yet. Keep adventuring!")
            return

        from rich.panel import Panel
        from rich.text import Text
        from rich import box

        for trait in traits:
            content = Text()
            content.append(f"{trait['name']}", style="bold cyan")
            content.append(f" (Tier {trait['tier']})\n", style="dim")
            content.append(f"{trait.get('description', '')}\n\n", style="")
            for effect in trait.get("effects", []):
                desc = format_effect_description(effect)
                content.append(f"  - {desc}\n", style="green")
            self.display.console.print(Panel(content, border_style="cyan", box=box.ROUNDED))

    def _show_combinations(self, repos: dict) -> None:
        """Show discovered spell combinations and custom spells."""
        spell_creation_repo = repos.get("spell_creation")
        if not spell_creation_repo or not self.character:
            self.display.show_info("No spell discoveries yet.")
            return

        char_id = self.character.get("id", "")

        # Discovered combinations
        combo_ids = spell_creation_repo.get_discovered_combinations(self.game_id, char_id)
        if combo_ids:
            from text_rpg.mechanics.spell_combinations import SPELL_COMBINATIONS
            self.display.show_info("--- Discovered Combinations ---")
            for cid in combo_ids:
                combo = SPELL_COMBINATIONS.get(cid)
                if combo:
                    self.display.show_info(f"  {combo.name}: {combo.element_a} + {combo.element_b}")

        # Custom spells
        customs = spell_creation_repo.get_custom_spells(self.game_id, char_id)
        if customs:
            self.display.show_info("--- Invented Spells ---")
            for cs in customs:
                level_str = f"Level {cs['level']}" if cs["level"] > 0 else "Cantrip"
                self.display.show_info(f"  {cs['name']} ({level_str} {cs.get('school', 'evocation')}): {cs['description']}")

        if not combo_ids and not customs:
            self.display.show_info("No spell discoveries yet. Try 'combine fire and wind' or 'invent spell that creates a shield of ice'.")

    def _show_guild_info(self, repos: dict) -> None:
        """Show guild membership status, ranks, and perks."""
        if not self.character:
            return

        guild_repo = repos.get("guild")
        if not guild_repo:
            self.display.show_info("Guild system not available.")
            return

        char_id = self.character.get("id", "")
        memberships = guild_repo.get_memberships(self.game_id, char_id)

        if not memberships:
            self.display.show_info("You are not a member of any guild. Find a guild representative to join!")
            return

        from text_rpg.content.loader import load_all_guilds
        from text_rpg.mechanics.guilds import get_guild_rank, get_rank_perks

        guilds = load_all_guilds()
        rep_repo = repos.get("reputation")
        trade_repo = repos.get("trade_skill")

        from rich.panel import Panel
        from rich.text import Text
        from rich import box

        for m in memberships:
            guild_id = m["guild_id"]
            guild_data = guilds.get(guild_id, {})
            guild_name = guild_data.get("name", guild_id)
            profession = guild_data.get("profession", "unknown")
            faction_id = guild_data.get("faction_id", "")

            # Get current reputation and trade level
            rep = 0
            if rep_repo and faction_id:
                rep = rep_repo.get_faction_rep(self.game_id, faction_id)

            trade_level = 1
            if trade_repo and profession:
                skill = trade_repo.get_skill(self.game_id, char_id, profession)
                if skill:
                    trade_level = skill.get("level", 1)

            current_rank = get_guild_rank(rep, trade_level, guild_data.get("ranks", []))
            perks = get_rank_perks(guild_data, current_rank)
            primary = " (Primary)" if m.get("is_primary") else ""

            content = Text()
            content.append(f"{guild_name}{primary}\n", style="bold cyan")
            content.append(f"Profession: {profession.capitalize()}\n", style="")
            content.append(f"Rank: {current_rank.capitalize()}\n", style="bold yellow")
            content.append(f"Reputation: {rep} | Trade Level: {trade_level}\n", style="dim")
            content.append(f"\nPerks:\n", style="bold")
            if perks["shop_discount"] > 0:
                content.append(f"  Shop discount: {int(perks['shop_discount'] * 100)}%\n", style="green")
            if perks["xp_multiplier"] > 1.0:
                content.append(f"  Craft XP: x{perks['xp_multiplier']:.1f}\n", style="green")
            if perks["dc_reduction"] > 0:
                content.append(f"  DC reduction: -{perks['dc_reduction']}\n", style="green")
            if perks["crit_chance"] > 0:
                content.append(f"  Crit chance: {int(perks['crit_chance'] * 100)}%\n", style="green")

            # Show active work orders for this guild
            orders = guild_repo.get_active_orders_for_guild(self.game_id, char_id, guild_id)
            if orders:
                content.append(f"\nActive Orders: {len(orders)}\n", style="bold")
                for order in orders:
                    reqs = order.get("requirements", {})
                    prog = order.get("progress", {})
                    req_str = ", ".join(
                        f"{prog.get(k, 0)}/{v} {k.replace('_', ' ')}" for k, v in reqs.items()
                    )
                    content.append(f"  - {order.get('description', order['template_id'])}: {req_str}\n", style="")

            completed = guild_repo.get_completed_count(self.game_id, char_id, guild_id)
            content.append(f"\nCompleted orders: {completed}\n", style="dim")

            self.display.console.print(Panel(content, border_style="cyan", box=box.ROUNDED))

    def _show_job_board(self, repos: dict) -> None:
        """Show available work orders based on guild membership and rank."""
        if not self.character:
            return

        guild_repo = repos.get("guild")
        if not guild_repo:
            self.display.show_info("Guild system not available.")
            return

        char_id = self.character.get("id", "")
        memberships = guild_repo.get_memberships(self.game_id, char_id)

        if not memberships:
            self.display.show_info("Join a guild first to see available work orders.")
            return

        from text_rpg.content.loader import load_all_guilds, load_work_order_templates
        from text_rpg.mechanics.guilds import get_guild_rank, rank_index

        guilds = load_all_guilds()
        templates = load_work_order_templates()
        rep_repo = repos.get("reputation")
        trade_repo = repos.get("trade_skill")

        lines = ["--- Job Board ---"]
        count = 0

        for tmpl in templates:
            tmpl_guild_id = tmpl.get("guild_id", "")
            membership = next((m for m in memberships if m["guild_id"] == tmpl_guild_id), None)
            if not membership:
                continue

            guild_data = guilds.get(tmpl_guild_id, {})
            faction_id = guild_data.get("faction_id", "")
            profession = guild_data.get("profession", "")

            rep = 0
            if rep_repo and faction_id:
                rep = rep_repo.get_faction_rep(self.game_id, faction_id)

            trade_level = 1
            if trade_repo and profession:
                skill = trade_repo.get_skill(self.game_id, char_id, profession)
                if skill:
                    trade_level = skill.get("level", 1)

            rank = get_guild_rank(rep, trade_level, guild_data.get("ranks", []))
            min_rank = tmpl.get("min_rank", "initiate")
            if rank_index(rank) < rank_index(min_rank):
                continue

            count += 1
            req_str = ", ".join(
                f"{qty}x {item.replace('_', ' ')}" for item, qty in tmpl.get("requirements", {}).items()
            )
            lines.append(
                f"  {count}. [{guild_data.get('name', tmpl_guild_id)}] {tmpl['name']} "
                f"({tmpl['order_type']}) — {req_str}"
            )
            lines.append(f"     {tmpl.get('description', '')}")
            lines.append(
                f"     Reward: {tmpl.get('reward_gold_min', 0)}-{tmpl.get('reward_gold_max', 0)} gold, "
                f"{tmpl.get('reward_xp', 0)} XP, +{tmpl.get('reward_rep', 0)} rep"
            )

        if count == 0:
            lines.append("  No orders available for your current ranks.")

        # Show active orders
        active = guild_repo.get_active_orders(self.game_id, char_id)
        if active:
            lines.append(f"\n--- Active Orders ({len(active)}/2) ---")
            for order in active:
                reqs = order.get("requirements", {})
                prog = order.get("progress", {})
                req_str = ", ".join(
                    f"{prog.get(k, 0)}/{v} {k.replace('_', ' ')}" for k, v in reqs.items()
                )
                lines.append(f"  - {order.get('description', order['template_id'])}: {req_str}")

        lines.append("\nUse 'accept job <number>' to take an order, 'submit job' to turn in.")
        self.display.show_info("\n".join(lines))

    def _show_bounty(self, repos: dict) -> None:
        """Show active bounties."""
        bounties = repos["reputation"].get_all_bounties(self.game_id)
        self.display.show_bounty(bounties)

    def _learn_starting_spells(self, char_dict: dict, repos: dict) -> None:
        """Learn starting spells for spellcaster classes."""
        char_class = char_dict.get("char_class", "").lower()
        casting_ability = char_dict.get("spellcasting_ability")
        if not casting_ability:
            return

        from text_rpg.content.loader import load_all_spells
        all_spells = load_all_spells()
        spell_repo = repos.get("spell")
        if not spell_repo:
            return

        game_id = char_dict["game_id"]
        char_id = char_dict["id"]

        # Get all spells for this class
        class_cantrips = [s for s in all_spells.values() if s.get("level") == 0 and char_class in s.get("classes", [])]
        class_level1 = [s for s in all_spells.values() if s.get("level") == 1 and char_class in s.get("classes", [])]

        # Load class data for cantrips_known count
        from text_rpg.content.loader import load_all_classes
        classes = load_all_classes()
        cls_data = classes.get(char_class, {})
        cantrips_known = cls_data.get("cantrips_known", 3)

        if char_class == "wizard":
            # Wizard: cantrips + 6 level-1 spells (spellbook), all prepared
            for cantrip in class_cantrips[:cantrips_known]:
                spell_repo.learn_spell(game_id, char_id, cantrip["id"])
                spell_repo.prepare_spell(game_id, char_id, cantrip["id"])
            for spell in class_level1[:6]:
                spell_repo.learn_spell(game_id, char_id, spell["id"])
                spell_repo.prepare_spell(game_id, char_id, spell["id"])
        elif char_class in ("cleric", "druid"):
            # Prepared casters: cantrips + ALL level-1 class spells, all prepared
            for cantrip in class_cantrips[:cantrips_known]:
                spell_repo.learn_spell(game_id, char_id, cantrip["id"])
                spell_repo.prepare_spell(game_id, char_id, cantrip["id"])
            for spell in class_level1:
                spell_repo.learn_spell(game_id, char_id, spell["id"])
                spell_repo.prepare_spell(game_id, char_id, spell["id"])
        elif char_class in ("bard", "sorcerer", "warlock"):
            # Known casters: cantrips + limited level-1 spells
            for cantrip in class_cantrips[:cantrips_known]:
                spell_repo.learn_spell(game_id, char_id, cantrip["id"])
                spell_repo.prepare_spell(game_id, char_id, cantrip["id"])
            known_limit = 4 if char_class == "bard" else (2 if char_class == "sorcerer" else 2)
            for spell in class_level1[:known_limit]:
                spell_repo.learn_spell(game_id, char_id, spell["id"])
                spell_repo.prepare_spell(game_id, char_id, spell["id"])
        elif char_class in ("paladin", "ranger"):
            # Half casters: no cantrips, no spells at level 1 (gain at level 2)
            pass

    def _show_spells(self, repos: dict) -> None:
        """Show known/prepared spells and spell slot status."""
        if not self.character:
            return
        casting_ability = self.character.get("spellcasting_ability")
        if not casting_ability:
            self.display.show_info("You don't know how to cast spells.")
            return

        spell_repo = repos.get("spell")
        if not spell_repo:
            return

        from text_rpg.content.loader import load_all_spells
        all_spells = load_all_spells()
        known_ids = spell_repo.get_known_spells(self.game_id, self.character["id"])
        prepared_ids = spell_repo.get_prepared_spells(self.game_id, self.character["id"])

        slots_remaining = safe_json(self.character.get("spell_slots_remaining"), {})
        slots_max = safe_json(self.character.get("spell_slots_max"), {})

        known_spells = [all_spells[sid] for sid in known_ids if sid in all_spells]
        self.display.show_spells(
            known_spells, prepared_ids, slots_remaining, slots_max,
            self.character.get("concentration_spell"),
        )

    def _handle_rewind(self, repos: dict, turn_loop: Any) -> None:
        """Handle the rewind/time travel command."""
        from text_rpg.engine.snapshots import StateSerializer
        from text_rpg.mechanics.time_travel import RESTORE_PRESETS

        snapshot_repo = repos.get("snapshot")
        if not snapshot_repo:
            self.display.show_error("Time travel is not available.")
            return

        snapshot = snapshot_repo.get_latest(self.game_id)
        if not snapshot:
            self.display.show_error("No snapshots available. You need to rest or explore more first.")
            return

        confirm = self.display.get_input(
            f"Rewind to turn {snapshot['turn_number']}? "
            f"Your stats and inventory will be preserved, but the world will reset. (y/n) > "
        )
        if confirm.lower() not in ("y", "yes"):
            self.display.show_info("You remain in the present.")
            return

        try:
            config = RESTORE_PRESETS["artifact"]
            serializer = StateSerializer(repos)
            serializer.restore(self.game_id, snapshot, config)

            game = repos["save_game"].get_game(self.game_id)
            loop_count = game.get("loop_count", 1)
            serializer.record_canon_entry(self.game_id, snapshot["id"], "rewind", loop_count)
        except Exception:
            logger.exception("Rewind failed")
            self.display.show_error("The threads of time resist you. The rewind failed; the present remains unchanged.")
            return

        # End any active conversation
        turn_loop.end_conversation()

        # Refresh character
        self.character = repos["character"].get_by_game(self.game_id)

        self.display.console.print(
            f"\n[bold magenta]The world shifts around you. You feel a sense of deja vu...[/bold magenta]"
            f"\n[dim]Loop {loop_count} — rewound to turn {snapshot['turn_number']}[/dim]\n"
        )

        # Show the restored location
        location = repos["location"].get(snapshot["location_id"], self.game_id)
        if location:
            self._show_location(location, repos)

    def _maybe_reindex_rag(self, repos: dict) -> None:
        """Rebuild vectors when the current embedding model's store is empty
        but the events ledger is not (i.e. after an embedding provider switch)."""
        try:
            if not self.indexer.is_available:
                return
            ledger_count = repos["event_ledger"].count(self.game_id)
            if ledger_count == 0:
                return
            if self.vector_store.has_any("events", {"game_id": self.game_id}):
                return
            self.display.show_info(
                "Rebuilding the GM's memory for the current embedding model "
                f"({ledger_count} events)... this happens once after switching providers."
            )
            self.indexer.reindex_from_db(repos, self.game_id)
        except Exception as e:
            logger.warning(f"RAG reindex check failed: {e}")

    def _save_game(self, repos: dict, turn_number: int) -> None:
        """Save current game state."""
        repos["save_game"].update_turn(self.game_id, turn_number)

    def _try_continue(self) -> bool:
        """Try to continue the most recent game."""
        repos = self._get_repos()
        games = repos["save_game"].list_games()
        if not games:
            self.display.show_info("No saved games found. Start a new game!")
            return False
        latest = games[0]
        self.game_id = latest["id"]
        self.character = repos["character"].get_by_game(self.game_id)
        if not self.character:
            self.display.show_error("Save is corrupted.")
            return False
        self.display.show_success(f"Continuing: {self.character['name']}")
        self._run_game_loop()
        return True

    def _seed_rag(self) -> None:
        """Seed RAG with SRD reference data if available."""
        try:
            from text_rpg.rag.seed_data.loader import load_seed_data

            if self.indexer.is_available:
                docs = load_seed_data()
                if docs:
                    self.indexer.index_seed_data(docs)
                    logger.info(f"Seeded RAG with {len(docs)} documents.")
        except Exception as e:
            logger.debug(f"RAG seeding skipped: {e}")

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
