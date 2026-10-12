"""Post-turn event handlers — quest notifications, reputation changes, bounties."""
from __future__ import annotations

from typing import Any

from text_rpg.utils import safe_json


class PostTurnEventHandler:
    """Handles post-turn events: quest notifications, reputation changes."""

    def __init__(self, game_id: str, repos: dict[str, Any], display: Any):
        self.game_id = game_id
        self.repos = repos
        self.display = display

    def process(self, result: Any) -> None:
        """Process all post-turn events from a turn result."""
        self._check_quest_events(result)
        self._check_reputation_events(result)
        self._check_guild_events(result)

    def _check_quest_events(self, result: Any) -> None:
        """Detect quest-related events and show notifications."""
        if not result.events:
            return
        for event in result.events:
            event_type = event.get("event_type", "")
            details = safe_json(event.get("mechanical_details"), {})

            if event_type in ("DIRECTOR_QUEST_AVAILABLE", "DIRECTOR_QUEST_FOLLOW_UP"):
                quest_name = details.get("quest_name", "")
                if quest_name:
                    self.display.show_quest_notification(quest_name, "new")
            elif event_type == "QUEST_START":
                quest_name = details.get("quest_name", "")
                if quest_name:
                    self.display.show_quest_notification(quest_name, "updated")
            elif event_type == "QUEST_UPDATE":
                objective = details.get("objective", "")
                quest_name = details.get("quest_name", "")
                if quest_name:
                    self.display.console.print(
                        f"\n  [cyan]Objective complete:[/cyan] {objective or event.get('description', '')}"
                    )
            elif event_type == "QUEST_COMPLETE":
                quest_name = details.get("quest_name", "A quest")
                self.display.show_quest_notification(quest_name, "completed")
                reward_bits = []
                if details.get("xp"):
                    reward_bits.append(f"+{details['xp']} XP")
                if details.get("gold"):
                    reward_bits.append(f"+{details['gold']} gold")
                for item in details.get("items", []):
                    reward_bits.append(item.replace("_", " ").title())
                if reward_bits:
                    self.display.console.print(
                        f"  [green]Rewards: {', '.join(reward_bits)}[/green]"
                    )
            elif event_type == "QUEST_FAILED":
                quest_name = details.get("quest_name", "")
                if quest_name:
                    self.display.console.print(
                        f"\n  [red]Quest failed: {quest_name}[/red]"
                    )
            elif event_type == "QUEST_NEGOTIATION":
                if details.get("accepted"):
                    quest_name = details.get("quest_name", "")
                    if quest_name:
                        self.display.show_quest_notification(
                            f"{quest_name} (terms negotiated)", "updated"
                        )
            elif event_type == "STORY_BEAT":
                story_name = details.get("story_name", "")
                beat_name = details.get("beat_name", "")
                if story_name:
                    self.display.show_story_notification(story_name, beat_name)

    def _check_reputation_events(self, result: Any) -> None:
        """Check combat events for reputation/bounty consequences."""
        if not result.events:
            return
        rep_repo = self.repos.get("reputation")
        if not rep_repo:
            return

        for event in result.events:
            event_type = event.get("event_type", "")
            details = safe_json(event.get("mechanical_details"), {})

            if event_type == "DEATH":
                self._handle_kill_event(event, rep_repo)
            elif event_type == "QUEST_COMPLETE":
                law_faction, trade_faction = self._get_region_factions()
                if law_faction:
                    rep_repo.adjust_faction_rep(self.game_id, law_faction, 5)
                if trade_faction:
                    rep_repo.adjust_faction_rep(self.game_id, trade_faction, 3)
            elif event_type == "QUEST_FAILED":
                law_faction, trade_faction = self._get_region_factions()
                if law_faction:
                    rep_repo.adjust_faction_rep(self.game_id, law_faction, -5)
                if trade_faction:
                    rep_repo.adjust_faction_rep(self.game_id, trade_faction, -3)

    def _handle_kill_event(self, event: dict, rep_repo: Any) -> None:
        """Handle reputation/bounty consequences of killing an entity."""
        target_id = event.get("target_id", "")
        entity = self.repos["entity"].get(target_id) if target_id else None
        if not entity:
            return

        faction_id = entity.get("faction_id")
        is_hostile = entity.get("is_hostile", False)
        law_faction, _ = self._get_region_factions()
        region = self._get_current_region()

        if is_hostile:
            if law_faction:
                rep_repo.adjust_faction_rep(self.game_id, law_faction, 3)
        elif faction_id:
            rep_repo.adjust_faction_rep(self.game_id, faction_id, -15)
            npc_name = entity.get("name", "someone")
            if region:
                rep_repo.add_bounty(self.game_id, region, 25, f"Murder of {npc_name}")
                self.display.console.print(
                    f"\n  [red]Your actions have consequences. Bounty added in the region.[/red]"
                )
        else:
            npc_name = entity.get("name", "someone")
            if region:
                rep_repo.add_bounty(self.game_id, region, 15, f"Murder of {npc_name}")
            if law_faction:
                rep_repo.adjust_faction_rep(self.game_id, law_faction, -10)

    def _check_guild_events(self, result: Any) -> None:
        """Handle guild-related events: work order completion, rank-up."""
        if not result.events:
            return
        for event in result.events:
            event_type = event.get("event_type", "")
            details = safe_json(event.get("mechanical_details"), {})

            if event_type == "WORK_ORDER_COMPLETE":
                guild_name = details.get("guild_name", "your guild")
                reward_gold = details.get("reward_gold", 0)
                self.display.console.print(
                    f"\n  [green]Work order complete! +{reward_gold} gold[/green]"
                )
            elif event_type == "GUILD_RANK_UP":
                new_rank = details.get("new_rank", "")
                if new_rank:
                    self.display.console.print(
                        f"\n  [bold yellow]Guild Rank Up: {new_rank.capitalize()}![/bold yellow]"
                    )
            elif event_type == "GUILD_JOINED":
                guild_name = details.get("guild_name", "a guild")
                self.display.console.print(
                    f"\n  [cyan]Joined {guild_name}![/cyan]"
                )

    def _get_current_region(self) -> str:
        """Get the region_id for the player's current location ("" if unknown)."""
        game = self.repos["save_game"].get_game(self.game_id)
        loc_id = game.get("current_location_id", "") if game else ""
        loc = self.repos["location"].get(loc_id, self.game_id) if loc_id else None
        return loc.get("region_id", "") if loc else ""

    def _get_region_factions(self) -> tuple[str | None, str | None]:
        """Resolve the (law, trade) faction ids for the player's current region."""
        region = self._get_current_region()
        if not region:
            return None, None
        try:
            from text_rpg.content.loader import load_all_factions
            factions = load_all_factions()
        except Exception:
            factions = {}

        region_factions = [
            fid for fid, data in factions.items() if data.get("region") == region
        ]
        # Generated regions have no TOML factions; generated NPCs use this convention.
        if not region_factions:
            return f"{region}_guard", f"{region}_merchants"

        law = next(
            (f for f in region_factions
             if any(k in f for k in ("guard", "watch", "legion", "warden", "militia", "order"))),
            None,
        )
        trade = next(
            (f for f in region_factions
             if f != law and any(k in f for k in ("merchant", "trade", "cooperative", "consortium", "guild", "company"))),
            None,
        )
        # A region always has some civic consequence: fall back to any faction
        # rather than silently dropping reputation changes.
        remaining = [f for f in region_factions if f not in (law, trade)]
        if law is None:
            law = remaining.pop(0) if remaining else trade
        if trade is None:
            trade = remaining.pop(0) if remaining else law
        return law, trade
