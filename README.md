# Consulta documental segura (RAG + MCP)

Prototipo de un endpoint para que los empleados de la entidad financiera consulten documentos
internos mediante RAG. Aplica RBAC por rol, filtros de metadatos antes de la recuperación,
herramientas vía MCP (simulado) y defensas contra prompt injection indirecta.

- Arquitectura y diagramas: [docs/architecture.md](docs/architecture.md) (borrador original: [docs/arquitectura-borrador.png](docs/arquitectura-borrador.png))
- Criterios del reto: [docs/challenge-requirements.md](docs/challenge-requirements.md)
- Plan y decisiones: [docs/implementation-plan.md](docs/implementation-plan.md)
- Flujo de prueba vía API (paso a paso y matriz de trazabilidad): [docs/api-test-flow.md](docs/api-test-flow.md). Automatizado en `scripts/verify_api.sh`

## Inicio rápido

Sin credenciales de Azure, la app usa `FakeLLM`, un modelo determinista y offline.

```bash
git clone https://github.com/GaboV593/ai-engineer-challenge.git && cd ai-engineer-challenge
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

Abre <http://localhost:8000/docs> (Swagger) o, en otra terminal, ejecuta la verificación completa (41 checks):

```bash
scripts/verify_api.sh
```

## Prerrequisitos

| Herramienta | Uso | Nota |
|---|---|---|
| Python **3.12** | aplicación | Con pyenv, `.python-version` selecciona 3.12 (`pyenv install 3.12` si no lo tienes). Sin pyenv, usa cualquier `python3.12`. |
| `curl`, `jq` | ejemplos y `scripts/verify_api.sh` | Vienen con macOS. En Linux: `apt install curl jq`. |
| Azure OpenAI (opcional) | modelo real | Endpoint, API key y deployment. Sin ellos se usa `FakeLLM`. |

En Windows, reemplaza `.venv/bin/` por `.venv\Scripts\`. El script de verificación requiere bash (Git Bash o WSL).

## Configuración (`.env`)

`cp .env.example .env` y ajusta:

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `LLM_PROVIDER` | `auto`: Azure si están las 3 variables de Azure, si no FakeLLM. `azure`: exige credenciales y falla al arrancar si falta alguna. `fake`: siempre FakeLLM. | `auto` |
| `AZURE_OPENAI_ENDPOINT` | URL del recurso, **sin** `/openai/v1`. Ej.: `https://<recurso>.services.ai.azure.com` o `https://<recurso>.openai.azure.com` | — |
| `AZURE_OPENAI_API_KEY` | API key del recurso | — |
| `AZURE_OPENAI_DEPLOYMENT` | Nombre del deployment (se envía como `model`) | `gpt-4.1-mini-1` |
| `LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M` | Tarifas USD por 1M tokens, usadas para el costo en la auditoría | `0.40` / `1.60` |
| `AUDIT_LOG_PATH` | Archivo JSONL de auditoría (el directorio se crea solo) | `logs/audit.jsonl` |

El cliente usa `OpenAI(base_url=f"{endpoint}/openai/v1/")` y envía la key como `Authorization: Bearer` y como `api-key`.
Al arrancar, el log indica el modo: si ves `Usando FakeLLM determinista…`, no se están usando las credenciales de Azure.

Para forzar un modo sin editar `.env` (las variables de entorno tienen prioridad):

```bash
LLM_PROVIDER=fake .venv/bin/uvicorn app.main:app --reload
```

## Ejecución y pruebas

| Comando | Qué hace |
|---|---|
| `.venv/bin/uvicorn app.main:app --reload` | Levanta la API en `http://localhost:8000` |
| `.venv/bin/pytest -q` | 23 tests offline y deterministas (no usan la red ni `.env`) |
| `.venv/bin/pytest -m live -s` | 6 pruebas end-to-end contra Azure OpenAI (consumen tokens; requieren `.env`) |
| `scripts/verify_api.sh` | Recorre todos los requisitos vía HTTP contra el servidor en marcha (PASS/FAIL) |

## API

| Método y ruta | Auth | Descripción |
|---|---|---|
| `POST /api/v1/docs/query` | `X-API-Key` | Consulta RAG |
| `POST /api/v1/docs/actions/{confirmation_id}/confirm` | `X-API-Key` | Confirma una acción sensible pendiente |
| `GET /health` | — | Liveness |
| `GET /docs` | — | Swagger UI |

**Request** de `/query`. El `employee_id` debe coincidir con la credencial, y `role`/`area` con el registro; si no, responde 403:

```json
{"employee_id": "E001", "role": "comercial", "area": "creditos", "query": "texto"}
```

**Response:** `trace_id`, `status` (`ok` | `blocked` | `pending_confirmation`), `answer`,
`documents[]` (doc_id, title, area, classification, source, sanitized), `security_flags[]`
(kind, source, detail), `pending_action` y `usage` (llm_calls, tokens, cost_usd, latency_ms).
Cada respuesta incluye el header `X-Trace-Id`.

**Códigos:** `200` ok · `401` credencial ausente o inválida · `403` identidad, rol o área no coinciden, rol sin política o confirmación inválida · `422` body inválido.

## Ejemplos

Credenciales de prueba (`app/adapters/outbound/mock_data.py`):

| Clave | Empleado | Rol | Área | Máx. clasificación | Tools |
|---|---|---|---|---|---|
| `dev-key-e001` | E001 | comercial | creditos | interno | search |
| `dev-key-e002` | E002 | analista | riesgos | confidencial | search, permissions |
| `dev-key-e003` | E003 | gerente | creditos | restringido | search, permissions, export ⚠ |
| `dev-key-e005` | E005 | pasante | creditos | sin política → 403 | — |

Consulta que recupera el documento del proveedor con la inyección, que se devuelve neutralizada (`sanitized: true` y flag `prompt_injection`):

```bash
curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e001' -H 'Content-Type: application/json' -d '{"employee_id":"E001","role":"comercial","area":"creditos","query":"¿Cuáles son los requisitos para otorgar un crédito de consumo?"}' | jq
```

Rol declarado que no coincide con el registro (403):

```bash
curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e001' -H 'Content-Type: application/json' -d '{"employee_id":"E001","role":"gerente","area":"creditos","query":"requisitos de crédito"}'
```

Acción sensible. Se solicita, queda en `pending_confirmation` y el dueño la confirma:

```bash
CID=$(curl -s -X POST localhost:8000/api/v1/docs/query -H 'X-API-Key: dev-key-e003' -H 'Content-Type: application/json' -d '{"employee_id":"E003","role":"gerente","area":"creditos","query":"Exporta los documentos DOC-CRE-001 y DOC-CRE-004."}' | jq -r '.pending_action.confirmation_id') && echo "$CID"
```

```bash
curl -s -X POST "localhost:8000/api/v1/docs/actions/$CID/confirm" -H 'X-API-Key: dev-key-e003' | jq
```

Auditoría: un evento por paso, todos con el mismo `trace_id`:

```bash
tail -n 20 logs/audit.jsonl | jq -c '{trace_id, event, tool, prompt_tokens, cost_usd, latency_ms}'
```

El paso a paso completo, con el resultado esperado de cada requisito, está en [docs/api-test-flow.md](docs/api-test-flow.md).

## Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| `pyenv: version '3.12' not installed` | `pyenv install 3.12`, o crea el venv con otro `python3.12` |
| `RuntimeError: LLM_PROVIDER=azure pero faltan variables…` | Completa las variables indicadas en `.env` o usa `LLM_PROVIDER=auto`/`fake` |
| El log dice `Usando FakeLLM` teniendo key | Con `auto` se requieren las 3 variables `AZURE_OPENAI_*` no vacías |
| `401` desde Azure en la consulta | Key o endpoint incorrectos: el endpoint va sin `/openai/v1` |
| `404` / `DeploymentNotFound` desde Azure | `AZURE_OPENAI_DEPLOYMENT` debe ser el nombre del deployment, no del modelo |
| `401 Credencial inválida` en la API | Falta el header `X-API-Key` o la clave no está en la tabla de ejemplos |
| `403 Acceso denegado` | `employee_id`, `role` o `area` del body no coinciden con la credencial. El motivo exacto está en `logs/audit.jsonl` (evento `access_denied`) |
| `address already in use` | Otro proceso usa el puerto 8000: `--port 8001` y `BASE_URL=http://localhost:8001 scripts/verify_api.sh` |
| `verify_api.sh`: `Servidor no disponible` | Levanta primero la API en otra terminal |

## Estructura

```
app/
├── domain/            modelos, políticas RBAC y guardrails (puro, sin I/O)
├── application/       puertos (Protocol), servicio orquestador, tools, prompts, memoria
├── adapters/inbound   FastAPI
├── adapters/outbound  LLM (Azure / fake), cliente + servidor MCP mock, directorio, auditoría
├── bootstrap.py       composition root
└── main.py            app ASGI
docs/                  requisitos, plan, arquitectura, flujo de prueba vía API
scripts/verify_api.sh  verificación automática de requisitos vía HTTP
tests/                 pytest (offline + live)
```

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
