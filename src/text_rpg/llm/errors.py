"""Typed LLM failure modes.

The engine distinguishes two failure classes so it never fabricates outcomes:

- LLMUnavailableError: the provider could not be reached (network, auth,
  timeout, rate limit). Surface to the player as a real error; the action
  must not be adjudicated and no events may be recorded.
- LLMMalformedOutputError: the provider responded, but the output stayed
  invalid after retries. Callers fall back to a safe refusal ("nothing
  happens") without writing fake events.
"""
from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM failures."""


class LLMUnavailableError(LLMError):
    """The LLM provider could not be reached or refused the request."""


class LLMMalformedOutputError(LLMError):
    """The LLM responded but produced invalid output after all retries."""
