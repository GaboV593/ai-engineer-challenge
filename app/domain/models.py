"""Modelos de dominio: entidades puras, sin dependencias de infraestructura."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Classification(StrEnum):
    """Niveles de clasificación, ordenados de menor a mayor sensibilidad."""

    PUBLICO = "publico"
    INTERNO = "interno"
    CONFIDENCIAL = "confidencial"
    RESTRINGIDO = "restringido"

    @property
    def level(self) -> int:
        return list(Classification).index(self)

    def up_to(self) -> list[Classification]:
        """Clasificaciones con nivel menor o igual al actual."""
        return [c for c in Classification if c.level <= self.level]


DocumentSource = Literal["interno", "proveedor_externo"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Employee(_Frozen):
    """Identidad del empleado. Los permisos NO se guardan aquí: salen de su rol."""

    employee_id: str
    name: str
    role: str
    area: str
    email: str


class RolePolicy(_Frozen):
    max_classification: Classification
    allowed_tools: frozenset[str]


class EmployeePermissions(_Frozen):
    """Permisos efectivos = identidad del empleado + política de su rol."""

    employee_id: str
    role: str
    area: str
    max_classification: Classification | None
    allowed_tools: frozenset[str] = frozenset()

    @property
    def allowed_classifications(self) -> list[Classification]:
        return self.max_classification.up_to() if self.max_classification else []


class Document(_Frozen):
    doc_id: str
    title: str
    content: str
    area: str
    classification: Classification
    source: DocumentSource
    score: float = 0.0


class DocumentRef(_Frozen):
    """Referencia a un documento devuelta al usuario (sin contenido completo)."""

    doc_id: str
    title: str
    area: str
    classification: Classification
    source: DocumentSource
    sanitized: bool


class InjectionFinding(_Frozen):
    pattern: str
    excerpt: str


FlagKind = Literal[
    "prompt_injection",
    "pii_masked",
    "tool_denied",
    "unexpected_tool_args",
    "access_violation",
    "confirmation_required",
]


class SecurityFlag(_Frozen):
    kind: FlagKind
    source: str  # "user_query", un doc_id o un nombre de tool
    detail: str


class ToolCall(_Frozen):
    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(_Frozen):
    content: str | None
    tool_calls: list[ToolCall] = []
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class PendingAction(_Frozen):
    confirmation_id: str
    employee_id: str
    tool: str
    arguments: dict[str, Any]
    expires_at: float


class UsageSummary(BaseModel):
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class QueryResult(BaseModel):
    trace_id: str
    status: Literal["ok", "blocked", "pending_confirmation"]
    answer: str
    documents: list[DocumentRef] = []
    security_flags: list[SecurityFlag] = []
    pending_action: PendingAction | None = None
    usage: UsageSummary = UsageSummary()


class ConfirmResult(BaseModel):
    trace_id: str
    status: Literal["executed"]
    tool: str
    result: str


class AuthenticationError(Exception):
    """Credencial ausente o inválida (HTTP 401)."""


class AccessDeniedError(Exception):
    """Identidad válida pero sin autorización para la operación (HTTP 403)."""
