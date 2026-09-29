"""Composition root: único lugar que conoce las implementaciones concretas."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from app.adapters.inbound.api import build_api
from app.adapters.outbound.audit_jsonl import JsonlAuditLogger
from app.adapters.outbound.directory import InMemoryEmployeeDirectory
from app.adapters.outbound.llm_fake import FakeLLM
from app.adapters.outbound.mcp_client import MCPDocumentClient
from app.adapters.outbound.mcp_server_mock import MockMCPServer
from app.adapters.outbound.mock_data import API_KEYS, DOCUMENTS, EMPLOYEES
from app.application.memory import InMemoryConversationMemory, InMemoryPendingActionStore
from app.application.ports import LLMPort
from app.application.service import DocsQueryService
from app.application.tools import ToolExecutor

logger = logging.getLogger(__name__)


def build_llm() -> LLMPort:
    provider = os.getenv("LLM_PROVIDER", "auto").lower()
    required = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
    missing = [k for k in required if not os.getenv(k)]
    if provider == "azure" and missing:
        raise RuntimeError(f"LLM_PROVIDER=azure pero faltan variables en .env: {', '.join(missing)}")
    if provider == "azure" or (provider == "auto" and not missing):
        from app.adapters.outbound.llm_azure import AzureOpenAILLM

        return AzureOpenAILLM(
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            price_input_per_1m=float(os.getenv("LLM_PRICE_INPUT_PER_1M", "0.15")),
            price_output_per_1m=float(os.getenv("LLM_PRICE_OUTPUT_PER_1M", "0.60")),
        )
    logger.warning("Usando FakeLLM determinista (sin credenciales de Azure OpenAI).")
    return FakeLLM()


def create_app(*, llm: LLMPort | None = None, audit_path: str | Path | None = None) -> FastAPI:
    load_dotenv()
    audit = JsonlAuditLogger(audit_path or os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl"))
    directory = InMemoryEmployeeDirectory(EMPLOYEES, API_KEYS)
    mcp_server = MockMCPServer(DOCUMENTS)
    documents = MCPDocumentClient(transport=mcp_server.handle)

    service = DocsQueryService(
        llm=llm or build_llm(),
        tools=ToolExecutor(documents=documents, directory=directory),
        directory=directory,
        audit=audit,
        memory=InMemoryConversationMemory(),
        pending=InMemoryPendingActionStore(),
    )
    return build_api(service, audit)
