"""Herramientas expuestas al LLM.

Ninguna tool recibe parámetros de identidad, área o clasificación: esos valores se
inyectan desde la sesión autenticada (EmployeePermissions), nunca desde el modelo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.application.ports import DocumentSearchPort, EmployeeDirectoryPort
from app.application.prompts import render_search_results
from app.domain.guardrails import can_access, is_tool_allowed, mask_pii, neutralize
from app.domain.models import Document, DocumentRef, EmployeePermissions, SecurityFlag
from app.domain.policies import (
    EXPORT_DOCUMENTS,
    GET_EMPLOYEE_PERMISSIONS,
    SEARCH_DOCUMENTS,
    resolve_permissions,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


TOOL_SPECS: dict[str, ToolSpec] = {
    SEARCH_DOCUMENTS: ToolSpec(
        name=SEARCH_DOCUMENTS,
        description="Busca documentos internos relevantes. Los filtros de acceso los aplica el sistema.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Texto a buscar", "maxLength": 300}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    GET_EMPLOYEE_PERMISSIONS: ToolSpec(
        name=GET_EMPLOYEE_PERMISSIONS,
        description="Devuelve los permisos del empleado de la sesión actual.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    EXPORT_DOCUMENTS: ToolSpec(
        name=EXPORT_DOCUMENTS,
        description=(
            "Exporta documentos por id. Llámala directamente cuando el usuario pida exportar: "
            "el sistema solicita y valida la confirmación humana. No pidas confirmación por texto."
        ),
        parameters={
            "type": "object",
            "properties": {"doc_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
            "required": ["doc_ids"],
            "additionalProperties": False,
        },
    ),
}


@dataclass
class ToolOutcome:
    content: str  # lo que recibe el LLM como mensaje de rol "tool"
    documents: list[DocumentRef] = field(default_factory=list)
    flags: list[SecurityFlag] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    def __init__(
        self, documents: DocumentSearchPort, directory: EmployeeDirectoryPort, top_k: int = 4
    ) -> None:
        self._documents = documents
        self._directory = directory
        self._top_k = top_k

    # Tool 2 del reto: get_employee_permissions(employee_id). El servicio la usa con el
    # id de la credencial; el LLM la ve sin parámetros.
    def get_employee_permissions(self, employee_id: str) -> EmployeePermissions | None:
        employee = self._directory.get_employee(employee_id)
        return resolve_permissions(employee) if employee else None

    def schemas_for(self, perms: EmployeePermissions) -> list[dict[str, Any]]:
        """Solo se exponen al modelo las tools de la allowlist del rol."""
        return [spec.to_openai() for name, spec in TOOL_SPECS.items() if is_tool_allowed(name, perms)]

    def execute(self, name: str, args: dict[str, Any], perms: EmployeePermissions) -> ToolOutcome:
        spec = TOOL_SPECS.get(name)
        if spec is None:
            return ToolOutcome(content=f"ERROR: herramienta desconocida '{name}'.")

        flags: list[SecurityFlag] = []
        unexpected = set(args) - set(spec.parameters.get("properties", {}))
        if unexpected:
            # p. ej. el modelo intenta pasar area_filter="*": se ignora y se registra.
            flags.append(
                SecurityFlag(kind="unexpected_tool_args", source=name, detail=f"ignorados: {sorted(unexpected)}")
            )
            args = {k: v for k, v in args.items() if k not in unexpected}

        match name:
            case "search_documents":
                outcome = self._search(str(args.get("query", ""))[:300], perms)
            case "get_employee_permissions":
                outcome = self._permissions(perms)
            case "export_documents":
                outcome = self._export([str(d) for d in args.get("doc_ids", [])][:10], perms)
            case _:
                outcome = ToolOutcome(content=f"ERROR: herramienta sin implementación '{name}'.")
        outcome.flags[:0] = flags
        return outcome

    def _search(self, query: str, perms: EmployeePermissions) -> ToolOutcome:
        area_filter = perms.area
        classification_filter = [c.value for c in perms.allowed_classifications]
        raw_docs = self._documents.search(query, area_filter, classification_filter, self._top_k)

        flags: list[SecurityFlag] = []
        safe_docs: list[Document] = []
        refs: list[DocumentRef] = []
        for doc in raw_docs:
            if not can_access(doc, perms):
                flags.append(
                    SecurityFlag(kind="access_violation", source=doc.doc_id, detail="descartado tras recuperación")
                )
                continue
            content, findings = neutralize(doc.content)
            title, title_findings = neutralize(doc.title)
            findings += title_findings
            content, pii_content = mask_pii(content)
            title, pii_title = mask_pii(title)
            if findings:
                flags.append(
                    SecurityFlag(
                        kind="prompt_injection",
                        source=doc.doc_id,
                        detail="neutralizado: " + ", ".join(sorted({f.pattern for f in findings})),
                    )
                )
            if pii_content or pii_title:
                flags.append(
                    SecurityFlag(kind="pii_masked", source=doc.doc_id, detail=", ".join(sorted({*pii_content, *pii_title})))
                )
            safe_docs.append(doc.model_copy(update={"content": content, "title": title}))
            refs.append(
                DocumentRef(
                    doc_id=doc.doc_id,
                    title=title,
                    area=doc.area,
                    classification=doc.classification,
                    source=doc.source,
                    sanitized=bool(findings),
                )
            )

        return ToolOutcome(
            content=render_search_results(safe_docs),
            documents=refs,
            flags=flags,
            audit={
                "area_filter": area_filter,
                "classification_filter": classification_filter,
                "doc_ids": [r.doc_id for r in refs],
            },
        )

    def _permissions(self, perms: EmployeePermissions) -> ToolOutcome:
        data = {
            "role": perms.role,
            "area": perms.area,
            "max_classification": perms.max_classification,
            "allowed_tools": sorted(perms.allowed_tools),
        }
        return ToolOutcome(content=json.dumps(data, ensure_ascii=False))

    def _export(self, doc_ids: list[str], perms: EmployeePermissions) -> ToolOutcome:
        classification_filter = [c.value for c in perms.allowed_classifications]
        receipt = self._documents.export(doc_ids, perms.area, classification_filter)
        return ToolOutcome(content=json.dumps(receipt, ensure_ascii=False), audit={"receipt": receipt})
