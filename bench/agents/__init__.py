"""Agents that can be run in the sandbox.

Spec strings:
  naive | careful                       scripted baselines (no model, used to test the bench)
  anthropic[:model] | claude[:model]    a Claude model through the Messages API
  openai[:model] | gpt[:model]          a GPT model through the Responses API
  gemini[:model] | google[:model]       a Gemini model through the Gemini API
"""

from __future__ import annotations

from typing import Any, Optional

from .base import AgentResult
from .llm import DEFAULT_MODELS, LLMAgent
from .prompts import POLICIES, system_prompt
from .scripted import CarefulAgent, NaiveAgent

ALIASES = {"claude": "anthropic", "gpt": "openai", "google": "gemini"}


def make_agent(spec: str, policy: str = "baseline", effort: Optional[str] = None, client: Any = None):
    spec = spec.strip()
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy {policy!r}; choose one of {sorted(POLICIES)}")
    if spec in ("naive", "scripted:naive"):
        return NaiveAgent(policy)
    if spec in ("careful", "scripted:careful"):
        return CarefulAgent(policy)
    provider, _, model = spec.partition(":")
    provider = ALIASES.get(provider, provider)
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unknown agent {spec!r}. Use naive, careful, anthropic:<model>, openai:<model> "
                         f"or gemini:<model>.")
    return LLMAgent(provider, model or None, policy, effort, client)


__all__ = ["AgentResult", "CarefulAgent", "LLMAgent", "NaiveAgent", "make_agent", "system_prompt", "DEFAULT_MODELS"]
