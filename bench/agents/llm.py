"""Adapters that let Claude, GPT and Gemini models shop in the sandbox.

Each adapter runs the same loop: send the employee's request, execute in the
sandbox every tool call the model makes, send the results back, and stop when
the model stops calling tools, calls finish or escalate_to_human, or the run
hits its step limit. The model sees the tools and their results, never the
judge, the hints or the ground truth.

API keys come from the usual environment variables: ANTHROPIC_API_KEY,
OPENAI_API_KEY, GEMINI_API_KEY (or GOOGLE_API_KEY).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..world import TOOL_SPECS, World
from .base import AgentResult
from .prompts import system_prompt

# Defaults only; pass any model id with provider:model on the command line.
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-6.1-sol", "gemini": "gemini-3.8-flash"}
MAX_MODEL_TURNS = 40
MAX_OUTPUT_TOKENS = 16_000


def _clean(value: Any) -> Any:
    """Turn 2.0 into 2 (some providers send integers as floats) and copy maps."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): _clean(v) for k, v in dict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _dump(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


class LLMAgent:
    needs_hints = False

    def __init__(self, provider: str, model: Optional[str] = None, policy: str = "baseline",
                 effort: Optional[str] = None, client: Any = None, max_turns: int = MAX_MODEL_TURNS):
        if provider not in DEFAULT_MODELS:
            raise ValueError(f"Unknown provider {provider!r}; choose anthropic, openai or gemini")
        self.provider = provider
        self.model = model or DEFAULT_MODELS[provider]
        self.policy = policy
        self.effort = effort
        self.max_turns = max_turns
        self._client = client
        self.label = f"{self.model} · {policy}"

    def run(self, world: World, task: str, hints: Any = None) -> AgentResult:
        result = AgentResult()
        try:
            getattr(self, f"_run_{self.provider}")(world, task, system_prompt(self.policy), result)
        except Exception as exc:  # API errors, refusals, malformed responses
            result.error = f"{type(exc).__name__}: {exc}"[:2000]
            world.end("error")
            return result
        if not world.done:
            world.end("stopped")  # the model was still calling tools after max_turns
        return result

    def _note(self, world: World, text: str) -> str:
        text = (text or "").strip()
        if text:
            world.note("agent_message", text)
        return text

    # ------------------------------------------------------------------ Anthropic (Messages API)

    def _run_anthropic(self, world: World, task: str, system: str, result: AgentResult) -> None:
        import anthropic

        client = self._client or anthropic.Anthropic()
        tools = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                 for t in TOOL_SPECS]
        extra: Dict[str, Any] = {"output_config": {"effort": self.effort}} if self.effort else {}
        messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
        for _ in range(self.max_turns):
            resp = client.messages.create(model=self.model, max_tokens=MAX_OUTPUT_TOKENS, system=system,
                                          tools=tools, messages=messages, **extra)
            result.model_calls += 1
            result.input_tokens += getattr(resp.usage, "input_tokens", 0) or 0
            result.output_tokens += getattr(resp.usage, "output_tokens", 0) or 0
            text = self._note(world, "".join(b.text for b in resp.content if getattr(b, "type", "") == "text"))
            calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if not calls:
                world.end("finished" if text else "stopped", text)
                return
            # Thinking blocks must go back unchanged, so the whole content is appended.
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in calls:
                out = world.call(block.name, _clean(block.input or {}))
                item: Dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": _dump(out)}
                if "error" in out:
                    item["is_error"] = True
                results.append(item)
            messages.append({"role": "user", "content": results})
            if world.done:
                return

    # ------------------------------------------------------------------ OpenAI (Responses API)

    def _run_openai(self, world: World, task: str, system: str, result: AgentResult) -> None:
        import openai

        client = self._client or openai.OpenAI()
        tools = [{"type": "function", "name": t["name"], "description": t["description"],
                  "parameters": t["input_schema"], "strict": False} for t in TOOL_SPECS]
        base: Dict[str, Any] = {"model": self.model, "instructions": system, "tools": tools}
        if self.effort:
            base["reasoning"] = {"effort": self.effort}
        resp = client.responses.create(input=[{"role": "user", "content": task}], **base)
        for turn in range(self.max_turns):
            result.model_calls += 1
            usage = getattr(resp, "usage", None)
            result.input_tokens += getattr(usage, "input_tokens", 0) or 0
            result.output_tokens += getattr(usage, "output_tokens", 0) or 0
            text = self._note(world, getattr(resp, "output_text", "") or "")
            calls = [o for o in resp.output if getattr(o, "type", "") == "function_call"]
            if not calls:
                world.end("finished" if text else "stopped", text)
                return
            outputs = []
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    out = {"error": "INVALID_ARGUMENTS", "message": "Arguments are not valid JSON."}
                else:
                    out = world.call(call.name, _clean(args))
                outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": _dump(out)})
            if world.done or turn == self.max_turns - 1:
                return
            resp = client.responses.create(input=outputs, previous_response_id=resp.id, **base)

    # ------------------------------------------------------------------ Google (Gemini API)

    def _run_gemini(self, world: World, task: str, system: str, result: AgentResult) -> None:
        from google import genai
        from google.genai import types

        client = self._client or genai.Client()
        decls = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                           parameters_json_schema=t["input_schema"]) for t in TOOL_SPECS]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents: List[Any] = [types.Content(role="user", parts=[types.Part.from_text(text=task)])]
        for _ in range(self.max_turns):
            resp = client.models.generate_content(model=self.model, contents=contents, config=config)
            result.model_calls += 1
            um = getattr(resp, "usage_metadata", None)
            result.input_tokens += getattr(um, "prompt_token_count", 0) or 0
            result.output_tokens += (getattr(um, "candidates_token_count", 0) or 0) + \
                (getattr(um, "thoughts_token_count", 0) or 0)
            content = resp.candidates[0].content if resp.candidates else None
            parts = list(content.parts or []) if content is not None else []
            text = self._note(world, "".join(p.text for p in parts if p.text and not p.thought))
            calls = [p.function_call for p in parts if p.function_call is not None]
            if not calls:
                world.end("finished" if text else "stopped", text)
                return
            contents.append(content)  # keeps thought signatures
            replies = []
            for fc in calls:
                out = world.call(fc.name, _clean(fc.args or {}))
                payload = {"error": out} if "error" in out else {"result": out}
                replies.append(types.Part(function_response=types.FunctionResponse(id=fc.id, name=fc.name,
                                                                                   response=payload)))
            contents.append(types.Content(role="user", parts=replies))
            if world.done:
                return
