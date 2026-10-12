"""Processes and classifies player text input."""
from __future__ import annotations

import re
from typing import Any

# Conversation exit phrases — player wants to end dialogue with an NPC.
_CONVERSATION_EXIT = re.compile(
    r"^(?:goodbye|bye|farewell|see\s+you|see\s+ya|later|"
    r"leave|walk\s+away|end\s+conversation|stop\s+talking|"
    r"nevermind|never\s+mind|nothing|forget\s+it|"
    r"i(?:'ll|\s+will)?\s+(?:go|leave|be\s+going))[\s.!]*$",
    re.I,
)

# Action types that break out of conversation mode (non-dialogue actions).
_CONVERSATION_BREAK_ACTIONS = frozenset({
    "move", "attack", "use_item", "equip", "unequip", "rest", "dodge", "dash", "hide",
    "disengage", "talk", "cast_spell", "flee", "combat_item", "combat_spell", "class_ability",
    "combine_spell", "invent_spell", "pickup", "hunt", "danger",
    "options",
    "revival_options", "revival_quote", "revival_confirm", "revival_cancel",
})

# Order matters — more specific patterns must come before greedy ones like "look" and "search".
PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("options", "options", re.compile(r"^(?:options|suggest(?:ed)? actions?|what can i do here|what should i do)[?.!]*$", re.I)),
    ("hunt", "hunt", re.compile(r"^(?:hunt|seek (?:combat|trouble|enemies)|look for enemies)$", re.I)),
    ("danger", "danger", re.compile(r"^(?:danger|combat zones|where can i fight)[?.!]*$", re.I)),
    ("revival_options", "revival", re.compile(r"^(?:revival(?:\s+(?:options|help))?|resurrection\s+options)$", re.I)),
    ("revival_confirm", "revival", re.compile(r"^confirm\s+revival$", re.I)),
    ("revival_cancel", "revival", re.compile(r"^cancel\s+revival$", re.I)),
    ("revival_quote", "revival", re.compile(r"^(?:revival|revive|resurrect)\s+(.+)$", re.I)),
    # Meta commands first (exact matches + natural phrasing)
    ("inventory", "meta", re.compile(r"^(?:inventory|items|bag|backpack|(?:(?:look|check|open|show|view|what(?:'s|\s+is)\s+in)\s+(?:my\s+)?(?:inventory|bag|backpack|pack|items))|what\s+(?:do\s+i\s+have|am\s+i\s+carrying|(?:is|are)\s+in\s+my\s+(?:inventory|bag|pack|backpack|items)))(?:\s+(.+))?$", re.I)),
    ("inventory", "meta", re.compile(r"^i$", re.I)),  # standalone "i" only
    ("character", "meta", re.compile(r"^(?:character|stats|sheet|status|char|(?:(?:show|view|check)\s+(?:my\s+)?(?:character|stats|sheet|status)))$", re.I)),
    ("skills", "meta", re.compile(r"^(?:skills|proficiencies|abilities|(?:(?:show|view|check)\s+(?:my\s+)?(?:skills|proficiencies|abilities)))$", re.I)),
    ("help", "meta", re.compile(r"^(?:help|\?|how\s+to\s+play|commands)$", re.I)),
    ("save", "meta", re.compile(r"^(?:save|save\s+game)$", re.I)),
    ("quit", "meta", re.compile(r"^(?:quit|exit|q)$", re.I)),
    ("quests", "meta", re.compile(r"^(?:quests?|journal|log|(?:(?:show|view|check)\s+(?:my\s+)?(?:quests?|journal|log)))$", re.I)),
    ("reputation", "meta", re.compile(r"^(?:reputation|rep|standing|standings|factions?)$", re.I)),
    ("bounty", "meta", re.compile(r"^(?:bounty|bounties|wanted)$", re.I)),
    ("stories", "meta", re.compile(r"^(?:stories|story|arcs?|campaigns?)$", re.I)),
    ("traits", "meta", re.compile(r"^(?:traits?|perks?|passive(?:s|\s+abilities)?)$", re.I)),
    ("map", "meta", re.compile(r"^(?:map|world\s*map)$", re.I)),
    ("rewind", "meta", re.compile(r"^(?:rewind|time\s*travel|undo)$", re.I)),

    # Quest accept/decline (before movement/greedy patterns)
    ("accept_quest", "accept_quest", re.compile(r"^(?:accept|take(?:\s+on)?)\s+(?:the\s+)?(?:quest|job|task|contract|offer)(?:\s+(.+))?$", re.I)),
    ("accept_quest", "accept_quest", re.compile(r"^(?:i(?:'ll|\s+will)\s+(?:do\s+it|help(?:\s+you)?|take\s+it)|accept)$", re.I)),
    ("decline_quest", "decline_quest", re.compile(r"^(?:decline|refuse|reject)\s+(?:the\s+)?(?:quest|job|task|contract|offer)$", re.I)),
    ("decline_quest", "decline_quest", re.compile(r"^(?:no\s+thanks|not\s+interested|decline)$", re.I)),

    # Movement
    ("move", "move", re.compile(r"^(?:go|move|walk|head|travel)\s+(?:to\s+)?(.+)$", re.I)),
    ("move", "move", re.compile(r"^((?:north|south|east|west|northeast|northwest|southeast|southwest|n|s|e|w|up|down|ne|nw|se|sw)(?:\s+[1-9]\d*)?)$", re.I)),

    # Actions (greedy patterns last)
    ("attack", "attack", re.compile(r"^(?:attack|hit|strike|fight|kill|stab|slash)\s+(.+)$", re.I)),
    ("talk", "talk", re.compile(r"^(?:can\s+i\s+|let\s+me\s+|i\s+want\s+to\s+|i(?:'d|\s+would)\s+like\s+to\s+)?(?:talk|speak|chat)\s+(?:to|with)\s+(.+?)[\s?.!]*$", re.I)),
    ("equip", "equip", re.compile(r"^(?:equip|wear|wield|put\s+on)\s+(.+)$", re.I)),
    ("unequip", "unequip", re.compile(r"^(?:unequip|remove|take\s+off|doff)\s+(.+)$", re.I)),
    ("buy_home", "buy_home", re.compile(r"^(?:buy\s+home|buy\s+house|buy\s+property|purchase\s+home)$", re.I)),
    ("buy", "buy", re.compile(r"^(?:buy|purchase)\s+(.+)$", re.I)),
    ("sell", "sell", re.compile(r"^(?:sell)\s+(.+)$", re.I)),
    ("browse", "browse", re.compile(r"^(?:browse|shop|store|wares|merchandise)$", re.I)),
    ("invent_item", "invent_item", re.compile(r"^(?:invent|forge|create|fashion|craft)\s+(?:an?\s+)?item\s+(?:called\s+|named\s+)?(.+)$", re.I)),
    ("enchant", "enchant", re.compile(r"^enchant\s+(?:my\s+|the\s+)?(.+)$", re.I)),
    ("craft", "craft", re.compile(r"^(?:craft|brew|forge|cook|make)\s+(.+)$", re.I)),
    ("combine_spell", "combine_spell", re.compile(r"^(?:combine|merge|fuse|blend)\s+(.+?)\s+(?:and|with|\+)\s+(.+?)$", re.I)),
    ("invent_spell", "invent_spell", re.compile(r"^(?:invent|create|design|research)\s+(?:a\s+)?(?:spell|magic)\s+(?:that\s+|to\s+|of\s+)?(.+)$", re.I)),
    ("combinations", "meta", re.compile(r"^(?:combinations|combos?|discovered\s+spells?|custom\s+spells?|inventions?)$", re.I)),
    ("guild_info", "meta", re.compile(r"^(?:guild|guilds?|profession|professions?)(?:\s+(?:info|status|rank))?$", re.I)),
    ("job_board", "meta", re.compile(r"^(?:jobs?|work\s*orders?|contracts?|commissions?|job\s*board)$", re.I)),
    ("join_guild", "join_guild", re.compile(r"^(?:join|enroll|register)\s+(?:the\s+)?(?:guild|order|circle)\s*(.*)$", re.I)),
    ("accept_job", "accept_job", re.compile(r"^(?:accept|take)\s+(?:job|order|contract)\s*(.+)?$", re.I)),
    ("submit_job", "submit_job", re.compile(r"^(?:submit|turn\s+in|deliver)\s+(?:job|order|contract)\s*(.+)?$", re.I)),
    ("abandon_job", "abandon_job", re.compile(r"^(?:abandon|cancel|drop)\s+(?:job|order|contract)\s*(.+)?$", re.I)),
    ("cast_spell", "cast_spell", re.compile(r"^(?:cast)\s+(.+?)(?:\s+(?:on|at|against)\s+(.+))?$", re.I)),
    ("train", "train", re.compile(r"^(?:train|learn|study)\s+(.+)$", re.I)),
    ("spells", "meta", re.compile(r"^(?:spells?|spellbook|(?:(?:show|view|check)\s+(?:my\s+)?(?:spells?|spellbook)))$", re.I)),
    ("recipes", "meta", re.compile(r"^(?:recipes?|crafting|trade\s*skills?)(?:\s+(.+))?$", re.I)),
    ("pickup", "pickup", re.compile(r"^(?:pick\s+up|take(?!\s+out\b)|collect|grab)(?:\s+(.+?))?[\s.!?]*$", re.I)),
    ("puzzle", "puzzle", re.compile(r"^(?:solve|unlock|disarm|answer|pick\s+lock|pick)\s*(.*)$", re.I)),
    ("recruit", "recruit", re.compile(r"^(?:recruit|hire|enlist)\s+(.+)$", re.I)),
    ("dismiss", "dismiss", re.compile(r"^(?:dismiss|release|let\s+go(?:\s+of)?)\s+(.+)$", re.I)),
    ("give", "give", re.compile(r"^(?:give|gift|offer)\s+(.+?)(?:\s+to\s+(.+))?$", re.I)),
    ("store", "store", re.compile(r"^(?:store|stash|deposit)\s+(.+)$", re.I)),
    ("retrieve", "retrieve", re.compile(r"^(?:retrieve|withdraw|take\s+out)\s+(.+)$", re.I)),
    ("upgrade_home", "upgrade_home", re.compile(r"^(?:upgrade|improve)\s*(.*)$", re.I)),
    ("home", "meta", re.compile(r"^(?:home|house|housing)$", re.I)),
    ("use_item", "use_item", re.compile(r"^(?:use|drink|eat|consume|apply)\s+(.+)$", re.I)),
    ("rest", "rest", re.compile(r"^(?:rest|sleep|camp)(?:\s+(short|long))?$", re.I)),
    ("dodge", "dodge", re.compile(r"^(?:dodge|evade)$", re.I)),
    ("grapple", "combat", re.compile(r"^(?:grapple|wrestle|grab hold of)\s+(.+)$", re.I)),
    ("dash", "dash", re.compile(r"^(?:dash|run|sprint)$", re.I)),
    ("hide", "hide", re.compile(r"^(?:hide|sneak)$", re.I)),
    ("flee", "combat", re.compile(r"^(?:flee|escape|retreat)$", re.I)),
    ("disengage", "disengage", re.compile(r"^(?:disengage|withdraw)$", re.I)),

    # Numbered combat choices (1-6)
    ("attack", "combat", re.compile(r"^1$")),
    ("combat_spell", "combat", re.compile(r"^2$")),
    ("combat_item", "combat", re.compile(r"^3$")),
    ("flee", "combat", re.compile(r"^4$")),
    ("dodge", "combat", re.compile(r"^5$")),
    ("class_ability", "combat", re.compile(r"^6$")),

    # Class ability keywords
    ("class_ability", "combat", re.compile(r"^(?:rage|flurry|flurry\s+of\s+blows|stunning\s+strike|lay\s+on\s+hands|wild\s+shape|inspire|bardic\s+inspiration)$", re.I)),

    # Greedy patterns last — these will match almost anything starting with "look" or "search"
    ("look", "look", re.compile(r"^(?:look|examine|inspect|observe)(?:\s+(?:at|around)\s*)?(.*)$", re.I)),
    ("search", "search", re.compile(r"^(?:search|investigate|check|look for)(?:\s+(.+))?$", re.I)),
]

DIRECTION_MAP = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "u": "up", "d": "down",
}


class InputHandler:
    def classify(self, raw_input: str) -> dict[str, Any]:
        text = raw_input.strip()
        if not text:
            return {"action_type": None, "target": None, "parameters": {}, "is_meta": False, "raw_input": raw_input}

        for action_name, action_category, pattern in PATTERNS:
            match = pattern.match(text)
            if match:
                target = match.group(1).strip() if match.lastindex and match.group(1) else None
                if action_name == "move" and target:
                    direction, separator, branch = target.lower().partition(" ")
                    target = DIRECTION_MAP.get(direction, direction) + (separator + branch if separator else "")
                parameters: dict[str, Any] = {}
                if action_name == "rest":
                    parameters["rest_type"] = (target or "short").lower()
                    target = None
                elif action_name == "combine_spell":
                    # target = element_a (group 1), element_b = group 2
                    element_b = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else None
                    parameters["element_a"] = target or ""
                    parameters["element_b"] = element_b or ""
                elif action_name == "invent_spell":
                    # target = spell_concept (group 1)
                    parameters["spell_concept"] = target or ""
                elif action_name == "cast_spell":
                    # target = spell name (group 1), spell_target = group 2
                    spell_target = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else None
                    if spell_target:
                        parameters["spell_target"] = spell_target
                elif action_name == "give":
                    # target = item name (group 1), npc = group 2
                    item_name = target
                    npc_target = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else None
                    parameters["item_name"] = item_name or ""
                    if npc_target:
                        parameters["npc_name"] = npc_target
                        target = npc_target  # target_id = NPC name for system routing
                    else:
                        target = item_name
                elif action_name == "inventory" and target:
                    parameters = self._parse_inventory_args(target)
                    target = None
                return {
                    "action_type": action_name,
                    "target": target,
                    "parameters": parameters,
                    "is_meta": action_category == "meta",
                    "raw_input": raw_input,
                }

        return {"action_type": None, "target": None, "parameters": {}, "is_meta": False, "raw_input": raw_input}

    @staticmethod
    def _parse_inventory_args(args: str) -> dict[str, Any]:
        """Parse inventory subcommand arguments.

        Supports:
          inventory weapons / armor / potions / misc / tools / all
          inventory sort name / sort value / sort weight / sort type
          inventory sort value desc
          inventory weapons sort value desc
        """
        parts = args.lower().split()
        params: dict[str, Any] = {}

        _CATEGORIES = {"weapon", "weapons", "armor", "potion", "potions", "misc", "tool", "tools", "all"}
        _SORT_KEYS = {"name", "value", "weight", "type"}

        # Normalize category names to singular form
        _CATEGORY_MAP = {
            "weapons": "weapon", "armor": "armor", "potions": "potion",
            "misc": "misc", "tools": "tool", "all": "all",
            "weapon": "weapon", "potion": "potion", "tool": "tool",
        }

        i = 0
        while i < len(parts):
            token = parts[i]
            if token in _CATEGORIES:
                params["category"] = _CATEGORY_MAP.get(token, token)
            elif token == "sort" and i + 1 < len(parts):
                i += 1
                sort_key = parts[i]
                if sort_key in _SORT_KEYS:
                    params["sort_by"] = sort_key
                if i + 1 < len(parts) and parts[i + 1] in ("asc", "desc"):
                    i += 1
                    params["sort_desc"] = parts[i] == "desc"
            elif token in ("asc", "desc"):
                params["sort_desc"] = token == "desc"
            i += 1

        return params

    def is_conversation_exit(self, raw_input: str) -> bool:
        """Check if the input is an explicit conversation exit phrase."""
        return bool(_CONVERSATION_EXIT.match(raw_input.strip()))

    def should_break_conversation(self, raw_input: str) -> bool:
        """Check if the input is a clear non-dialogue action that should end conversation.

        Returns True for actions like move, attack, use_item — NOT for look/search/unknown.
        """
        classified = self.classify(raw_input)
        action_type = classified.get("action_type")
        return action_type in _CONVERSATION_BREAK_ACTIONS
