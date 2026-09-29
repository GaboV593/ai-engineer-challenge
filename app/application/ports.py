"""Puertos de salida (typing.Protocol). Los adaptadores los implementan por estructura."""

from __future__ import annotations

from typing import Any, Protocol

from app.domain.models import Document, Employee, LLMResponse, PendingAction


class LLMPort(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


class DocumentSearchPort(Protocol):
    """Búsqueda documental expuesta por el servidor MCP externo."""

    def search(
        self, query: str, area_filter: str, classification_filter: list[str], top_k: int
    ) -> list[Document]: ...

    def export(
        self, doc_ids: list[str], area_filter: str, classification_filter: list[str]
    ) -> dict[str, Any]: ...


class EmployeeDirectoryPort(Protocol):
    def authenticate(self, api_key: str) -> str | None:
        """Devuelve el employee_id asociado a la credencial, o None."""
        ...

    def get_employee(self, employee_id: str) -> Employee | None: ...


class AuditPort(Protocol):
    def log(self, trace_id: str, event: str, **fields: Any) -> None: ...


class ConversationMemoryPort(Protocol):
    def history(self, employee_id: str) -> list[dict[str, Any]]: ...

    def append(self, employee_id: str, user_text: str, assistant_text: str) -> None: ...


class PendingActionStorePort(Protocol):
    def save(self, action: PendingAction) -> None: ...

    def get(self, confirmation_id: str) -> PendingAction | None: ...

    def delete(self, confirmation_id: str) -> None: ...
