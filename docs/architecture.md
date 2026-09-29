# Arquitectura

Arquitectura hexagonal (puertos y adaptadores). El dominio no depende de nada; la aplicación
depende solo del dominio y de sus puertos (`typing.Protocol`); los adaptadores implementan
esos puertos y `bootstrap.py` los conecta (composition root).

## Borrador original

![Borrador de arquitectura hexagonal](arquitectura-borrador.png)

## Vista de componentes

```mermaid
flowchart LR
    EMP([Empleado])

    subgraph IN["Adaptadores de entrada"]
        API["API REST (FastAPI)<br/>POST /api/v1/docs/query<br/>POST /api/v1/docs/actions/{id}/confirm<br/>Auth X-API-Key · log de acceso"]
    end

    subgraph APP["Capa de aplicación"]
        SVC["Servicio orquestador<br/>DocsQueryService"]
        TOOLS["Tools<br/>search_documents(query)<br/>get_employee_permissions()<br/>export_documents(doc_ids) ⚠"]
        PROMPTS["Prompts<br/>system + &lt;untrusted_document&gt;"]
        MEM["Memoria<br/>conversación · acciones pendientes"]
        PORTS["Puertos (Protocol)<br/>LLMPort · DocumentSearchPort<br/>EmployeeDirectoryPort · AuditPort"]

        subgraph DOM["Capa de dominio"]
            MOD["Modelos<br/>Employee · RolePolicy · Document…"]
            GR["Guardrails + políticas<br/>ROLE_POLICIES · resolve_permissions<br/>detect_injection · neutralize<br/>mask_pii · can_access · allowlist"]
        end
    end

    subgraph OUT["Adaptadores de salida"]
        LLM["Modelo LLM<br/>Azure OpenAI v1 (openai SDK)<br/>· FakeLLM determinista"]
        MCP["Cliente MCP → Servidor MCP externo (simulado)<br/>mcp_search_documents(query, area_filter, classification_filter)"]
        DIR["Directorio de empleados (mock)"]
        AUD["Auditoría JSONL<br/>trace_id · tokens · costo · latencia"]
    end

    EMP --> API
    API -- "implementa puertos de entrada" --> SVC
    SVC --> TOOLS & PROMPTS & MEM & GR
    TOOLS --> GR
    SVC --> PORTS
    PORTS -. "implementan puertos de salida" .- LLM & MCP & DIR & AUD
```

## Flujo seguro de una consulta

```mermaid
sequenceDiagram
    autonumber
    actor E as Empleado
    participant API as FastAPI
    participant S as DocsQueryService
    participant G as Guardrails (dominio)
    participant L as LLM
    participant M as MCP (externo)
    participant A as Auditoría

    E->>API: POST /docs/query + X-API-Key
    API->>S: authenticate(api_key) → employee_id
    S->>S: permisos = directorio + ROLE_POLICIES
    Note over S: body.role/area ≠ registro → 403
    S->>G: detect_injection(query) — inyección directa
    alt patrón detectado
        S-->>API: status=blocked (no llega al LLM)
    end
    S->>G: mask_pii(query)
    S->>L: system prompt + query enmascarada + tools de la allowlist del rol
    L-->>S: tool_call(search_documents, {query})
    S->>G: is_tool_allowed? requires_human_confirmation?
    S->>M: mcp_search_documents(query, area_filter=sesión, classification_filter=sesión)
    Note over M: filtro de metadatos ANTES del ranking vectorial
    M-->>S: documentos (datos no confiables)
    S->>G: can_access · neutralize · mask_pii por documento
    S->>L: mensaje role=tool con <untrusted_document …>
    L-->>S: respuesta final
    S->>A: eventos con trace_id, tokens, costo, latencia
    S-->>API: answer + documents + security_flags + usage
```

## Controles de seguridad y dónde viven

| Control | Ubicación |
|---|---|
| Autenticación (API key → employee_id, comparación en tiempo constante) | `adapters/inbound/api.py`, `adapters/outbound/directory.py` |
| RBAC por rol, deny by default | `domain/policies.py` |
| Rol/área del body verificados contra el registro | `application/service.py::_authorize` |
| Filtros de metadatos antes de la recuperación | `application/tools.py::_search` → `mcp_server_mock.py` |
| Revalidación de acceso tras la recuperación | `domain/guardrails.py::can_access` |
| Allowlist en exposición y en ejecución | `tools.py::schemas_for`, `service.py` (bucle de tools) |
| Tools sin parámetros de identidad/área; args extra ignorados | `application/tools.py` |
| Inyección directa (query) bloqueada antes del LLM | `service.py` + `guardrails.detect_injection` |
| Inyección indirecta neutralizada por oración, sin bloquear el documento | `guardrails.neutralize` |
| Separación de datos e instrucciones (`<untrusted_document>`, escape de `< >`) | `application/prompts.py` |
| PII enmascarada antes del LLM | `guardrails.mask_pii` |
| Confirmación humana determinista (lista fija, uso único, TTL, dueño) | `policies.SENSITIVE_TOOLS`, `service.confirm` |
| Auditoría JSONL + log de acceso HTTP | `adapters/outbound/audit_jsonl.py`, middleware en `api.py` |
