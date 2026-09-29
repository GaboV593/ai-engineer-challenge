"""Servicio orquestador: autenticación, RBAC, guardrails, bucle de tools y auditoría."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.application.ports import (
    AuditPort,
    ConversationMemoryPort,
    EmployeeDirectoryPort,
    LLMPort,
    PendingActionStorePort,
)
from app.application.prompts import build_system_prompt
from app.application.tools import ToolExecutor
from app.domain.guardrails import detect_injection, is_tool_allowed, mask_pii, requires_human_confirmation
from app.domain.models import (
    AccessDeniedError,
    AuthenticationError,
    ConfirmResult,
    DocumentRef,
    EmployeePermissions,
    LLMResponse,
    PendingAction,
    QueryResult,
    SecurityFlag,
    UsageSummary,
)

MAX_TOOL_ROUNDS = 3
CONFIRMATION_TTL_SECONDS = 300


class DocsQueryService:
    def __init__(
        self,
        *,
        llm: LLMPort,
        tools: ToolExecutor,
        directory: EmployeeDirectoryPort,
        audit: AuditPort,
        memory: ConversationMemoryPort,
        pending: PendingActionStorePort,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._directory = directory
        self._audit = audit
        self._memory = memory
        self._pending = pending

    # ---------------------------------------------------------------- auth / RBAC
    def authenticate(self, api_key: str | None, trace_id: str) -> str:
        employee_id = self._directory.authenticate(api_key) if api_key else None
        if not employee_id:
            self._audit.log(trace_id, "auth_failed", reason="credencial ausente o inválida")
            raise AuthenticationError("credencial inválida")
        self._audit.log(trace_id, "auth_ok", employee_id=employee_id)
        return employee_id

    def _authorize(
        self, trace_id: str, authenticated_id: str, employee_id: str, role: str, area: str
    ) -> EmployeePermissions:
        """El body no es fuente de verdad: rol y área salen del directorio + política del rol."""

        def deny(reason: str) -> AccessDeniedError:
            self._audit.log(trace_id, "access_denied", employee_id=authenticated_id, reason=reason)
            return AccessDeniedError(reason)

        if employee_id != authenticated_id:
            raise deny("employee_id del body no coincide con la credencial")
        perms = self._tools.get_employee_permissions(authenticated_id)
        if perms is None:
            raise deny("empleado no registrado")
        if (role, area) != (perms.role, perms.area):
            raise deny(f"rol/área declarados ({role}/{area}) no coinciden con el registro")
        if not perms.allowed_tools:
            raise deny(f"el rol '{perms.role}' no tiene política asignada")

        self._audit.log(
            trace_id,
            "access_granted",
            employee_id=perms.employee_id,
            role=perms.role,
            area=perms.area,
            max_classification=perms.max_classification,
            allowed_tools=sorted(perms.allowed_tools),
        )
        return perms

    # ---------------------------------------------------------------- query
    def query(
        self, *, trace_id: str, authenticated_employee_id: str, employee_id: str, role: str, area: str, query: str
    ) -> QueryResult:
        started = time.perf_counter()
        perms = self._authorize(trace_id, authenticated_employee_id, employee_id, role, area)

        masked_query, pii_types = mask_pii(query)
        self._audit.log(trace_id, "query_received", employee_id=perms.employee_id, query=masked_query)

        flags: list[SecurityFlag] = []
        usage = UsageSummary()

        # 1) Inyección directa en la query: se bloquea antes de llegar al LLM.
        findings = detect_injection(query, include_document_patterns=False)
        if findings:
            patterns = sorted({f.pattern for f in findings})
            self._audit.log(trace_id, "injection_detected", source="user_query", patterns=patterns)
            flags.append(SecurityFlag(kind="prompt_injection", source="user_query", detail=", ".join(patterns)))
            return self._finish(
                trace_id, perms, started, usage, masked_query,
                QueryResult(
                    trace_id=trace_id,
                    status="blocked",
                    answer="La consulta fue bloqueada porque contiene patrones de prompt injection.",
                    security_flags=flags,
                ),
                remember=False,
            )

        # 2) PII enmascarada antes de enviar al LLM.
        if pii_types:
            flags.append(SecurityFlag(kind="pii_masked", source="user_query", detail=", ".join(pii_types)))

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(perms)},
            *self._memory.history(perms.employee_id),
            {"role": "user", "content": masked_query},
        ]
        tool_schemas = self._tools.schemas_for(perms)
        documents: dict[str, DocumentRef] = {}
        answer = "No fue posible completar la consulta dentro del límite de pasos permitido."

        # 3) Bucle de tool calling con validación de allowlist en cada llamada.
        for _ in range(MAX_TOOL_ROUNDS + 1):
            response = self._call_llm(trace_id, messages, tool_schemas, usage)
            if not response.tool_calls:
                answer = response.content or ""
                break

            messages.append(_assistant_message(response))
            for call in response.tool_calls:
                if not is_tool_allowed(call.name, perms):
                    self._audit.log(trace_id, "tool_denied", tool=call.name, role=perms.role, arguments=call.arguments)
                    flags.append(SecurityFlag(kind="tool_denied", source=call.name, detail="fuera de la allowlist del rol"))
                    messages.append(_tool_message(call.id, f"ERROR: la herramienta '{call.name}' no está permitida."))
                    continue

                if requires_human_confirmation(call.name):
                    action = PendingAction(
                        confirmation_id=uuid.uuid4().hex,
                        employee_id=perms.employee_id,
                        tool=call.name,
                        arguments=call.arguments,
                        expires_at=time.time() + CONFIRMATION_TTL_SECONDS,
                    )
                    self._pending.save(action)
                    self._audit.log(
                        trace_id, "confirmation_required", tool=call.name,
                        arguments=call.arguments, confirmation_id=action.confirmation_id,
                    )
                    flags.append(SecurityFlag(kind="confirmation_required", source=call.name, detail="acción sensible"))
                    return self._finish(
                        trace_id, perms, started, usage, masked_query,
                        QueryResult(
                            trace_id=trace_id,
                            status="pending_confirmation",
                            answer=(
                                f"La acción '{call.name}' requiere confirmación humana. Confirme con "
                                f"POST /api/v1/docs/actions/{action.confirmation_id}/confirm"
                            ),
                            documents=list(documents.values()),
                            security_flags=flags,
                            pending_action=action,
                        ),
                        remember=False,
                    )

                outcome = self._tools.execute(call.name, call.arguments, perms)
                self._audit.log(trace_id, "tool_call", tool=call.name, arguments=call.arguments, **outcome.audit)
                for flag in outcome.flags:
                    if flag.kind == "prompt_injection":
                        self._audit.log(trace_id, "injection_detected", source=flag.source, detail=flag.detail)
                flags.extend(outcome.flags)
                documents.update({d.doc_id: d for d in outcome.documents})
                messages.append(_tool_message(call.id, outcome.content))

        return self._finish(
            trace_id, perms, started, usage, masked_query,
            QueryResult(
                trace_id=trace_id,
                status="ok",
                answer=answer,
                documents=list(documents.values()),
                security_flags=flags,
            ),
        )

    # ---------------------------------------------------------------- confirmación humana
    def confirm(self, *, trace_id: str, authenticated_employee_id: str, confirmation_id: str) -> ConfirmResult:
        """Ejecuta exactamente la acción almacenada, sin volver a pasar por el LLM."""
        action = self._pending.get(confirmation_id)
        if action is None or action.employee_id != authenticated_employee_id:
            self._audit.log(trace_id, "confirmation_rejected", confirmation_id=confirmation_id, reason="no existe o es ajena")
            raise AccessDeniedError("confirmación inválida")
        self._pending.delete(confirmation_id)  # uso único
        if time.time() > action.expires_at:
            self._audit.log(trace_id, "confirmation_rejected", confirmation_id=confirmation_id, reason="expirada")
            raise AccessDeniedError("confirmación expirada")

        perms = self._tools.get_employee_permissions(authenticated_employee_id)
        if perms is None or not is_tool_allowed(action.tool, perms):
            self._audit.log(trace_id, "tool_denied", tool=action.tool, confirmation_id=confirmation_id)
            raise AccessDeniedError("herramienta no permitida")

        outcome = self._tools.execute(action.tool, action.arguments, perms)
        self._audit.log(
            trace_id, "sensitive_action_executed", tool=action.tool,
            confirmation_id=confirmation_id, employee_id=perms.employee_id, **outcome.audit,
        )
        return ConfirmResult(trace_id=trace_id, status="executed", tool=action.tool, result=outcome.content)

    # ---------------------------------------------------------------- helpers
    def _call_llm(
        self, trace_id: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], usage: UsageSummary
    ) -> LLMResponse:
        response = self._llm.complete(messages, tools)
        usage.llm_calls += 1
        usage.prompt_tokens += response.prompt_tokens
        usage.completion_tokens += response.completion_tokens
        usage.cost_usd = round(usage.cost_usd + response.cost_usd, 6)
        self._audit.log(
            trace_id,
            "llm_call",
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
            tool_calls=[c.name for c in response.tool_calls],
        )
        return response

    def _finish(
        self,
        trace_id: str,
        perms: EmployeePermissions,
        started: float,
        usage: UsageSummary,
        masked_query: str,
        result: QueryResult,
        *,
        remember: bool = True,
    ) -> QueryResult:
        usage.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        result.usage = usage
        if remember:
            self._memory.append(perms.employee_id, masked_query, result.answer)
        self._audit.log(
            trace_id,
            "query_completed",
            employee_id=perms.employee_id,
            status=result.status,
            doc_ids=[d.doc_id for d in result.documents],
            flags=[f"{f.kind}:{f.source}" for f in result.security_flags],
            llm_calls=usage.llm_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.prompt_tokens + usage.completion_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
        )
        return result


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
            }
            for c in response.tool_calls
        ],
    }


def _tool_message(tool_call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
