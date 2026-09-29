"""Adaptador LLM para Azure OpenAI (API v1) usando el SDK oficial de openai con base_url."""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from app.domain.models import LLMResponse, ToolCall


class AzureOpenAILLM:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        price_input_per_1m: float,
        price_output_per_1m: float,
        timeout: float = 30.0,
    ) -> None:
        self._client = OpenAI(
            base_url=f"{endpoint.rstrip('/')}/openai/v1/",
            api_key=api_key,
            default_headers={"api-key": api_key},  # header nativo de Azure, además del Bearer del SDK
            timeout=timeout,
            max_retries=2,
        )
        self._deployment = deployment
        self._price_in = price_input_per_1m
        self._price_out = price_output_per_1m

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self._deployment, "messages": messages}
        if tools:
            kwargs |= {"tools": tools, "tool_choice": "auto", "parallel_tool_calls": False}

        started = time.perf_counter()
        completion = self._client.chat.completions.create(**kwargs)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        message = completion.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=_parse_args(tc.function.arguments))
            for tc in (message.tool_calls or [])
            if tc.type == "function"
        ]
        usage = completion.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        cost = (prompt_tokens * self._price_in + completion_tokens * self._price_out) / 1_000_000
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            model=completion.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
        )


def _parse_args(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
