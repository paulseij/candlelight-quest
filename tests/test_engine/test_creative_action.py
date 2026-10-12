"""Tests for the dispatcher's creative-action path failure semantics."""
from __future__ import annotations

from text_rpg.engine.action_dispatcher import ActionDispatcher
from text_rpg.engine.system_registry import SystemRegistry
from text_rpg.llm.errors import LLMMalformedOutputError, LLMUnavailableError
from text_rpg.models.action import Action, ActionResult
from text_rpg.systems.base import GameContext


def _context():
    return GameContext(
        game_id="g1",
        character={
            "id": "char1", "name": "Hero", "level": 1,
            "ability_scores": "{}", "skill_proficiencies": "[]",
            "proficiency_bonus": 2,
        },
        location={"id": "loc1", "name": "Clearing"},
        entities=[],
        combat_state=None,
        inventory={"items": "[]"},
        recent_events=[],
        turn_number=1,
    )


def _action():
    return Action(action_type="custom", actor_id="char1", raw_input="juggle some rocks")


class _DownDirector:
    def evaluate_plausibility(self, action, context):
        raise LLMUnavailableError("provider down")


class _MalformedDirector:
    def evaluate_plausibility(self, action, context):
        raise LLMMalformedOutputError("still bad after retries")


class _GroundedDirector:
    """Returns a plausibility, then a canned grounded outcome."""

    def evaluate_plausibility(self, action, context):
        return {"plausibility": 0.9, "skill": "athletics", "ability": "strength"}

    def generate_creative_outcome(self, action, context, plausibility, repos,
                                  roll_total=0, dc=10, roll_succeeded=True):
        return ActionResult(
            action_id=action.id,
            success=roll_succeeded,
            outcome_description="You juggle the rocks.",
            events=[{"event_type": "CREATIVE_ACTION", "description": "juggled"}],
        )


def test_llm_unavailable_records_no_events():
    dispatcher = ActionDispatcher(SystemRegistry(), director=_DownDirector())
    result = dispatcher._try_creative_action(_action(), _context())
    assert result.success is False
    assert result.events == []
    assert result.state_mutations == []
    assert "unavailable" in result.outcome_description.lower()


def test_malformed_output_records_no_events():
    dispatcher = ActionDispatcher(SystemRegistry(), director=_MalformedDirector())
    result = dispatcher._try_creative_action(_action(), _context())
    assert result.success is False
    assert result.events == []
    assert "nothing comes of it" in result.outcome_description


def test_llm_unavailable_action_type_short_circuits():
    dispatcher = ActionDispatcher(SystemRegistry(), director=_GroundedDirector())
    action = Action(action_type="llm_unavailable", actor_id="char1", raw_input="???")
    result = dispatcher.dispatch(action, _context())
    assert result.success is False
    assert result.events == []
    assert "unavailable" in result.outcome_description.lower()


def test_grounded_outcome_used_for_success_and_failure():
    dispatcher = ActionDispatcher(SystemRegistry(), director=_GroundedDirector())
    result = dispatcher._try_creative_action(_action(), _context())
    # The director's grounded result comes back with dice attached.
    assert result.outcome_description == "You juggle the rocks."
    assert result.dice_rolls, "dice roll should be attached"
