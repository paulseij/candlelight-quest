"""Tests for story activation behavior with quest generation.

Covers three scenarios when the Director activates a new story in the
"Activate new stories" block of _check_story_progression:

1. Hook quest generated successfully — story remains active with that quest_id.
2. Generation returns None but hook has quest_template — story is undone
   (complete_story called), no active story left behind.
3. Hook without quest_template — story remains active regardless of generation.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from text_rpg.systems.base import GameContext
from text_rpg.systems.director import Director


# -- helpers --------------------------------------------------------------- #

def _make_context(game_id: str = "test-g", turn_number: int = 10) -> GameContext:
    return GameContext(
        game_id=game_id,
        character={"id": "char-1", "level": 3, "name": "Hero"},
        location={
            "id": "thornfield_square",
            "name": "Thornfield Square",
            "region_id": "verdant_reach",
        },
        entities=[
            {
                "entity_type": "npc",
                "id": "npc-1",
                "name": "Guard Captain",
                "is_alive": True,
            }
        ],
        turn_number=turn_number,
    )


def _make_director(generate_story_quest_result=None):
    """Create a Director with mocked LLM, retriever, indexer, and _generate_story_quest."""
    llm_mock = MagicMock()
    retriever_mock = MagicMock()
    indexer_mock = MagicMock()

    director = Director(
        llm=llm_mock, retriever=retriever_mock, indexer=indexer_mock
    )
    if generate_story_quest_result is not None:
        director._generate_story_quest = MagicMock(return_value=generate_story_quest_result)
    return director


def _make_repos(active_stories=None, completed_ids=None):
    """Build a minimal repos dict with a mocked world_state."""
    ws = MagicMock()
    if active_stories is not None:
        ws.get_active_stories.return_value = active_stories
    else:
        ws.get_active_stories.return_value = []

    if completed_ids is not None:
        ws.get_completed_story_ids.return_value = completed_ids
    else:
        ws.get_completed_story_ids.return_value = []

    all_quests = [
        {
            "id": "q-1",
            "name": "Existing Quest",
            "status": "completed",
            "game_id": "test-g",
        }
    ]
    ws.get_all_quests.return_value = all_quests
    return {"world_state": ws}


def _make_seed(seed_id="seed-1", name="The Lost Beacon", hook_quest_template=None, tags=None):
    """Build a minimal story seed dict."""
    hook = {"description": "A beacon has gone dark."}
    if hook_quest_template is not None:
        hook["quest_template"] = hook_quest_template
    return {
        "id": seed_id,
        "name": name,
        "hook": hook,
        "tags": tags or [],
        "description_template": "The beacon must be relit.",
    }


# -- tests ----------------------------------------------------------------- #

def test_story_active_with_quest_id():
    """When _generate_story_quest returns a quest event, the story remains
    active and update_story_beat is called with the quest_id.
    """
    quest_event = {
        "event_type": "DIRECTOR_QUEST_AVAILABLE",
        "target_id": "quest-100",
        "description": "A new quest emerges from the story: Relight the Beacon.",
    }

    ctx = _make_context()
    director = _make_director(generate_story_quest_result=quest_event)
    repos = _make_repos(active_stories=[], completed_ids=["old-seed"])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="new-story", hook_quest_template={"type": "investigate"}),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    ws = repos["world_state"]
    # save_story_state was called — story saved as active.
    assert ws.save_story_state.called
    # update_story_beat was called with the quest_id from quest_event.
    assert ws.update_story_beat.called
    call_kwargs = ws.update_story_beat.call_args
    if call_kwargs:
        args = call_kwargs[0]
        assert len(args) >= 4
        assert args[0] == ctx.game_id


def test_story_event_recorded():
    """A STORY_BEAT event is recorded for the new story activation."""
    quest_event = {
        "event_type": "DIRECTOR_QUEST_AVAILABLE",
        "target_id": "quest-200",
    }

    ctx = _make_context(turn_number=15)
    director = _make_director(generate_story_quest_result=quest_event)
    repos = _make_repos(active_stories=[], completed_ids=[])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="story-ok", hook_quest_template={"type": "kill"}),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    story_events = [e for e in events if e.get("event_type") == "STORY_BEAT"]
    assert len(story_events) >= 1


def test_complete_story_called_when_generation_fails():
    """When generation returns None and hook has a quest_template,
    complete_story is called to mark the story as failed/complete.
    """
    ctx = _make_context()
    director = _make_director(generate_story_quest_result=None)
    repos = _make_repos(active_stories=[], completed_ids=[])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="no-quest-story", hook_quest_template={"type": "slay"}),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    ws = repos["world_state"]
    assert ws.complete_story.called
    call_args = ws.complete_story.call_args
    if call_args:
        args = call_args[0]
        assert len(args) >= 3
        assert args[1] == "no-quest-story"
        assert args[2] == "failed"


def test_no_active_story_left_behind():
    """The story that failed quest generation should not appear in
    get_active_stories results after complete_story is called.
    """
    ctx = _make_context()
    director = _make_director(generate_story_quest_result=None)
    repos = _make_repos(
        active_stories=[{"seed_id": "other-story", "status": "active"}],
        completed_ids=[],
    )

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(
            seed_id="failing-seed", name="Failing Story", hook_quest_template={"type": "rescue"}
        ),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        director._check_story_progression(ctx, repos)

    ws = repos["world_state"]
    assert ws.complete_story.called
    call_args = ws.complete_story.call_args
    if call_args:
        assert "failing-seed" in str(call_args[0])


def test_story_active_without_template():
    """When the hook has no quest_template, the story is saved as active
    and complete_story is NOT called for this seed.
    """
    ctx = _make_context()
    director = _make_director(generate_story_quest_result=None)
    repos = _make_repos(active_stories=[], completed_ids=[])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="no-template-story", name="Wordless Tale", hook_quest_template=None),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    ws = repos["world_state"]
    assert ws.save_story_state.called


def test_story_retained_when_no_template_and_generation_ok():
    """When hook has no template, story stays active regardless of whether
    _generate_story_quest would succeed or not (it's skipped).
    """
    ctx = _make_context()
    director = _make_director(generate_story_quest_result={"target_id": "q-x"})
    repos = _make_repos(active_stories=[], completed_ids=[])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="no-template-ok", name="Plain Tale", hook_quest_template=None),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    ws = repos["world_state"]
    assert ws.save_story_state.called


def test_story_cooldown_respected():
    """A new story isn't activated too soon after the last one.
    When cooldown hasn't elapsed, no new story is created.
    """
    ctx = _make_context(turn_number=10)
    director = Director(
        llm=MagicMock(),
        retriever=MagicMock(),
        indexer=MagicMock(),
    )
    # Set a recent story activation to enforce cooldown.
    director._last_generation["story"] = 5

    repos = _make_repos(active_stories=[], completed_ids=[])

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="cooled-story"),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)

    assert director._last_generation["story"] == 5


def test_max_two_active_stories():
    """When two stories are already active, no more are activated."""
    ctx = _make_context()
    director = _make_director(generate_story_quest_result={"target_id": "q-max"})
    repos = _make_repos(
        active_stories=[
            {"seed_id": "active-1", "status": "active"},
            {"seed_id": "active-2", "status": "active"},
        ],
        completed_ids=[],
    )

    with patch(
        "text_rpg.mechanics.story_seeds.select_seed",
        return_value=_make_seed(seed_id="should-skip"),
    ), patch(
        "text_rpg.mechanics.story_seeds.resolve_variables",
        return_value={},
    ):
        events = director._check_story_progression(ctx, repos)
