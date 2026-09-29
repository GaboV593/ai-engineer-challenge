"""LLM determinista para demo y tests sin credenciales. Implementa LLMPort.

Se comporta como un modelo "ingenuo": ante una intención de exportar pide
export_documents aunque no esté entre las tools ofrecidas (simula un modelo
manipulado), para demostrar que la allowlist se valida en la ejecución.
"""

from __future__ import annotations

import copy
import json
import re
import time
from typing import Any

from app.domain.models import LLMResponse, ToolCall

_DOC_RE = re.compile(r'<untrusted_document id="([^"]+)"[^>]*>\nTítulo: ([^\n]*)\n(.*?)\n</untrusted_document>', re.S)


class FakeLLM:
    model = "fake-deterministic"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []  # mensajes recibidos (para inspección en tests)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        started = time.perf_counter()
        self.calls.append(copy.deepcopy(messages))
        offered = {t["function"]["name"] for t in tools}

        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        query = messages[last_user]["content"]
        turn = messages[last_user + 1 :]
        called = [
            tc["function"]["name"] for m in turn if m["role"] == "assistant" for tc in m.get("tool_calls") or []
        ]
        tool_outputs = "\n".join(m["content"] for m in turn if m["role"] == "tool")
        lowered = query.lower()

        content: str | None = None
        tool_call: ToolCall | None = None
        if not called:
            if "permiso" in lowered and "get_employee_permissions" in offered:
                tool_call = ToolCall(id="call_1", name="get_employee_permissions", arguments={})
            elif "search_documents" in offered:
                tool_call = ToolCall(id="call_1", name="search_documents", arguments={"query": query})
            else:
                content = "No tengo herramientas disponibles para responder."
        elif "export" in lowered and "export_documents" not in called:
            doc_ids = [m.group(1) for m in _DOC_RE.finditer(tool_outputs)]
            tool_call = ToolCall(id="call_2", name="export_documents", arguments={"doc_ids": doc_ids})
        else:
            content = _summarize(tool_outputs)

        prompt_tokens = len(json.dumps(messages, ensure_ascii=False)) // 4
        completion_tokens = len(content or "") // 4 + (20 if tool_call else 0)
        return LLMResponse(
            content=content,
            tool_calls=[tool_call] if tool_call else [],
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )


def _summarize(tool_outputs: str) -> str:
    docs = _DOC_RE.findall(tool_outputs)
    if not docs:
        return tool_outputs or "No encontré información autorizada para tu consulta."
    lines = [f"Encontré {len(docs)} documento(s) relevante(s):"]
    for doc_id, title, body in docs:
        lines.append(f"- [{doc_id}] {title}: {body[:140]}")
        if "[CONTENIDO NEUTRALIZADO" in body:
            lines.append(f"  Nota: parte del contenido de {doc_id} fue retirado por seguridad.")
    return "\n".join(lines)
