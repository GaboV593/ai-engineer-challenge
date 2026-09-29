# Consulta documental segura (RAG + MCP)

Prototipo de un endpoint para que los empleados de la entidad financiera consulten documentos
internos mediante RAG. Aplica RBAC por rol, filtros de metadatos antes de la recuperación,
herramientas vía MCP (simulado) y defensas contra prompt injection indirecta.

- Arquitectura y diagramas: [docs/architecture.md](docs/architecture.md)
- Criterios del reto: [docs/challenge-requirements.md](docs/challenge-requirements.md)
- Plan y decisiones: [docs/implementation-plan.md](docs/implementation-plan.md)
- Flujo de prueba vía API (paso a paso y matriz de trazabilidad): [docs/api-test-flow.md](docs/api-test-flow.md). Automatizado en `scripts/verify_api.sh`

## Estructura

```
app/
├── domain/            modelos, políticas RBAC y guardrails (puro, sin I/O)
├── application/       puertos (Protocol), servicio orquestador, tools, prompts, memoria
├── adapters/inbound   FastAPI
├── adapters/outbound  LLM (Azure / fake), cliente + servidor MCP mock, directorio, auditoría
├── bootstrap.py       composition root
└── main.py            app ASGI
```

## Puesta en marcha

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Sin credenciales de Azure se usa `FakeLLM`, un modelo determinista que permite probar todo sin conexión.
Para usar Azure OpenAI completa `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` y `AZURE_OPENAI_DEPLOYMENT`.
El cliente usa `OpenAI(base_url=f"{endpoint}/openai/v1/")`.

```bash
.venv/bin/uvicorn app.main:app --reload
```

```bash
.venv/bin/pytest -q
```

La suite anterior es offline y determinista. Las pruebas end-to-end contra Azure OpenAI consumen tokens y requieren `.env`:

```bash
.venv/bin/pytest -m live -s
```

## Ejemplos

Credenciales de prueba (`app/adapters/outbound/mock_data.py`):

| Clave | Empleado | Rol | Área | Máx. clasificación | Tools |
|---|---|---|---|---|---|
| `dev-key-e001` | E001 | comercial | creditos | interno | search |
| `dev-key-e002` | E002 | analista | riesgos | confidencial | search, permissions |
| `dev-key-e003` | E003 | gerente | creditos | restringido | search, permissions, export ⚠ |
| `dev-key-e005` | E005 | pasante | creditos | sin política → 403 | — |

Consulta que recupera el documento del proveedor con la inyección. El documento se entrega con esa oración neutralizada:

```bash
curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e001' -H 'Content-Type: application/json' -d '{"employee_id":"E001","role":"comercial","area":"creditos","query":"¿Cuáles son los requisitos para otorgar un crédito de consumo?"}'
```

Rol declarado que no coincide con el registro (403):

```bash
curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e001' -H 'Content-Type: application/json' -d '{"employee_id":"E001","role":"gerente","area":"creditos","query":"requisitos de crédito"}'
```

Acción sensible. La respuesta es `pending_confirmation` con un `confirmation_id`:

```bash
curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e003' -H 'Content-Type: application/json' -d '{"employee_id":"E003","role":"gerente","area":"creditos","query":"Exporta los documentos de requisitos de crédito"}'
```

```bash
curl -s -X POST localhost:8000/api/v1/docs/actions/<confirmation_id>/confirm -H 'X-API-Key: dev-key-e003'
```

La auditoría queda en `logs/audit.jsonl`: un evento por paso, todos con el mismo `trace_id` que se devuelve en el header `X-Trace-Id`.

## Decisiones y trade-offs

- **Identidad:** la identidad sale de la credencial. El `role` y el `area` del body solo se verifican (403 si no coinciden) y nunca deciden el acceso.
- **RBAC:** es por rol (`ROLE_POLICIES`) y niega por defecto. El rol define el nivel de clasificación y las tools; el área del empleado define el dominio de documentos.
- **Filtros:** los aplica el servidor MCP antes del ranking y fallan cerrados si no llegan. Después se revalidan con `can_access`, como defensa en profundidad.
- **Tools:** el LLM solo pasa `query` o `doc_ids`. Los argumentos extra, como `area_filter`, se ignoran y quedan marcados. Las tools `mcp_search_documents(query, area_filter, classification_filter)` y `get_employee_permissions(employee_id)` del reto existen en la frontera MCP y en el servicio, pero no se exponen así al modelo.
- **Inyección directa:** si la query del usuario contiene un patrón de inyección, se bloquea antes del LLM.
- **Inyección indirecta:** se neutraliza solo la oración sospechosa y el documento legítimo se entrega. El contenido va envuelto en `<untrusted_document>` con `<`/`>` escapados, y el system prompt declara que ese contenido es dato, no instrucción.
- **Confirmación humana:** la necesidad de confirmar sale de una lista fija, no del modelo. Se ejecuta exactamente la acción almacenada, sin volver a pasar por el LLM. La confirmación es de uso único, expira en 5 minutos y solo la puede usar el dueño.
- **Limitaciones del prototipo:**
  - La detección es heurística (regex ES/EN). En producción se complementaría con un clasificador, como Azure AI Content Safety Prompt Shields.
  - La búsqueda "vectorial" es un coseno sobre bag-of-words.
  - La memoria y las acciones pendientes viven en el proceso.
  - Las API keys están en el código mock. En producción se usaría OIDC/JWT con el IdP corporativo.
