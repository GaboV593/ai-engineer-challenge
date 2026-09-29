"""Cliente MCP (JSON-RPC 2.0). Implementa DocumentSearchPort sobre un transporte inyectable."""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.domain.models import Document

logger = logging.getLogger(__name__)

Transport = Callable[[str], str]  # request JSON -> response JSON (stdio/HTTP en un MCP real)


class MCPError(RuntimeError):
    pass


class MCPDocumentClient:
    def __init__(self, transport: Transport) -> None:
        self._send = transport
        self._ids = itertools.count(1)
        self._rpc("initialize", {"clientInfo": {"name": "docs-query-agent", "version": "0.1"}})
        self._available = {t["name"] for t in self._rpc("tools/list", {})["tools"]}

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        response = json.loads(self._send(json.dumps(request)))
        if "error" in response:
            raise MCPError(response["error"].get("message", "error MCP"))
        return response["result"]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._available:
            raise MCPError(f"el servidor MCP no expone '{name}'")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = result["content"][0]["text"]
        if result.get("isError"):
            raise MCPError(text)
        return json.loads(text)

    def search(
        self, query: str, area_filter: str, classification_filter: list[str], top_k: int
    ) -> list[Document]:
        raw = self._call_tool(
            "mcp_search_documents",
            {"query": query, "area_filter": area_filter, "classification_filter": classification_filter, "top_k": top_k},
        )
        documents: list[Document] = []
        for item in raw:  # la respuesta del servidor externo también es no confiable
            try:
                documents.append(Document.model_validate(item))
            except ValidationError:
                logger.warning("documento MCP con esquema inválido descartado: %s", item.get("doc_id"))
        return documents

    def export(self, doc_ids: list[str], area_filter: str, classification_filter: list[str]) -> dict[str, Any]:
        return self._call_tool(
            "mcp_export_documents",
            {"doc_ids": doc_ids, "area_filter": area_filter, "classification_filter": classification_filter},
        )
