"""Adaptador de entrada HTTP (FastAPI): autenticación por API key, DTOs y log de acceso."""

import time
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

from app.application.ports import AuditPort
from app.application.service import DocsQueryService
from app.domain.models import AccessDeniedError, AuthenticationError, ConfirmResult, QueryResult


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(pattern=r"^E\d{3,6}$", examples=["E001"])
    role: str = Field(min_length=2, max_length=40, examples=["comercial"])
    area: str = Field(min_length=2, max_length=40, examples=["creditos"])
    query: str = Field(min_length=3, max_length=500, examples=["¿Requisitos para un crédito de consumo?"])


def build_api(service: DocsQueryService, audit: AuditPort) -> FastAPI:
    app = FastAPI(title="Consulta documental segura (RAG + MCP)", version="0.1.0")
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    @app.middleware("http")
    async def access_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        trace_id = uuid.uuid4().hex
        request.state.trace_id = trace_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            audit.log(trace_id, "unhandled_error", path=request.url.path)
            response = JSONResponse({"detail": "Error interno", "trace_id": trace_id}, status_code=500)
        response.headers["X-Trace-Id"] = trace_id
        audit.log(
            trace_id,
            "http_access",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            client_ip=request.client.host if request.client else None,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    def current_employee(request: Request, api_key: Annotated[str | None, Depends(api_key_header)]) -> str:
        try:
            return service.authenticate(api_key, request.state.trace_id)
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="Credencial inválida", headers={"WWW-Authenticate": "ApiKey"})

    Employee = Annotated[str, Depends(current_employee)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/docs/query", response_model=QueryResult)
    def query_docs(body: QueryRequest, request: Request, employee_id: Employee) -> QueryResult:
        try:
            return service.query(
                trace_id=request.state.trace_id,
                authenticated_employee_id=employee_id,
                employee_id=body.employee_id,
                role=body.role,
                area=body.area,
                query=body.query,
            )
        except AccessDeniedError:
            # El motivo detallado queda en la auditoría; al cliente no se le filtra.
            raise HTTPException(status_code=403, detail="Acceso denegado")

    @app.post("/api/v1/docs/actions/{confirmation_id}/confirm", response_model=ConfirmResult)
    def confirm_action(confirmation_id: str, request: Request, employee_id: Employee) -> ConfirmResult:
        try:
            return service.confirm(
                trace_id=request.state.trace_id,
                authenticated_employee_id=employee_id,
                confirmation_id=confirmation_id,
            )
        except AccessDeniedError:
            raise HTTPException(status_code=403, detail="Confirmación inválida o expirada")

    return app
