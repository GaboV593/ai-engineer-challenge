from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.outbound.llm_fake import FakeLLM
from app.bootstrap import create_app
from app.domain.models import LLMResponse, ToolCall

KEYS = {"E001": "dev-key-e001", "E002": "dev-key-e002", "E003": "dev-key-e003", "E005": "dev-key-e005"}
PROFILES = {
    "E001": ("comercial", "creditos"),
    "E002": ("analista", "riesgos"),
    "E003": ("gerente", "creditos"),
    "E005": ("pasante", "creditos"),
}


class ScriptedLLM:
    """LLM que emite una secuencia fija de tool calls (simula un modelo manipulado)."""

    def __init__(self, script: list[ToolCall]) -> None:
        self._script = list(script)
        self.calls: list[list[dict[str, Any]]] = []
        self.offered_tools: set[str] = set()

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append(messages)
        self.offered_tools = {t["function"]["name"] for t in tools}
        if self._script:
            return LLMResponse(content=None, tool_calls=[self._script.pop(0)], model="scripted", prompt_tokens=10, completion_tokens=5)
        return LLMResponse(content="fin", model="scripted", prompt_tokens=10, completion_tokens=5)


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def make_client(audit_path: Path) -> Callable[..., TestClient]:
    def _make(llm: Any) -> TestClient:
        return TestClient(create_app(llm=llm, audit_path=audit_path))

    return _make


@pytest.fixture
def client(make_client: Callable[..., TestClient], fake_llm: FakeLLM) -> TestClient:
    return make_client(fake_llm)


@pytest.fixture
def ask(client: TestClient) -> Callable[..., Any]:
    def _ask(employee_id: str, query: str, *, http: TestClient | None = None, **overrides: str) -> Any:
        role, area = PROFILES[employee_id]
        body = {"employee_id": employee_id, "role": role, "area": area, "query": query, **overrides}
        return (http or client).post("/api/v1/docs/query", json=body, headers={"X-API-Key": KEYS[employee_id]})

    return _ask


@pytest.fixture
def read_audit(audit_path: Path) -> Callable[[], list[dict[str, Any]]]:
    def _read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in audit_path.read_text().splitlines()]

    return _read
