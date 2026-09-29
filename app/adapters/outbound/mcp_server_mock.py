"""Servidor MCP externo simulado (JSON-RPC 2.0 sobre strings, in-process).

Expone mcp_search_documents(query, area_filter, classification_filter) con filtrado de
metadatos ANTES del ranking "vectorial" (coseno sobre bag-of-words).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from typing import Any

_STOPWORDS = {
    "los", "las", "del", "para", "con", "por", "que", "una", "son", "cual", "cuales", "como",
    "sus", "mas", "este", "esta", "the", "and", "for", "segun",
}

TOOLS = [
    {
        "name": "mcp_search_documents",
        "description": "Búsqueda semántica con filtros obligatorios de área y clasificación.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "area_filter": {"type": "string"},
                "classification_filter": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "default": 4},
            },
            "required": ["query", "area_filter", "classification_filter"],
        },
    },
    {
        "name": "mcp_export_documents",
        "description": "Genera un paquete de exportación con los documentos autorizados.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_ids": {"type": "array", "items": {"type": "string"}},
                "area_filter": {"type": "string"},
                "classification_filter": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["doc_ids", "area_filter", "classification_filter"],
        },
    },
]


def _tokens(text: str) -> Counter[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return Counter(t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 2 and t not in _STOPWORDS)


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(a[t] * b[t] for t in a.keys() & b.keys())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


class MockMCPServer:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._docs = documents
        self._vectors = {d["doc_id"]: _tokens(f"{d['title']} {d['content']}") for d in documents}

    def handle(self, raw_request: str) -> str:
        req = json.loads(raw_request)
        try:
            result = self._dispatch(req.get("method"), req.get("params") or {})
            return json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result})
        except ValueError as exc:
            return json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32602, "message": str(exc)}})

    def _dispatch(self, method: str | None, params: dict[str, Any]) -> dict[str, Any]:
        match method:
            case "initialize":
                return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "docs-mcp-mock", "version": "0.1"}}
            case "tools/list":
                return {"tools": TOOLS}
            case "tools/call":
                return self._call_tool(params.get("name"), params.get("arguments") or {})
        raise ValueError(f"método no soportado: {method}")

    def _call_tool(self, name: str | None, args: dict[str, Any]) -> dict[str, Any]:
        area = args.get("area_filter")
        classes = args.get("classification_filter")
        if not area or not classes:  # fail closed: sin filtros no hay búsqueda
            return {"isError": True, "content": [{"type": "text", "text": "filtros de área y clasificación obligatorios"}]}

        # 1) Filtro de metadatos ANTES de la recuperación vectorial.
        candidates = [d for d in self._docs if d["area"] == area and d["classification"] in classes]

        if name == "mcp_search_documents":
            q = _tokens(str(args.get("query", "")))
            scored = [(_cosine(q, self._vectors[d["doc_id"]]), d) for d in candidates]
            ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: s[0], reverse=True)
            top_k = int(args.get("top_k", 4))
            payload: Any = [{**d, "score": round(s, 4)} for s, d in ranked[:top_k]]
        elif name == "mcp_export_documents":
            allowed = {d["doc_id"] for d in candidates}
            requested = [str(i) for i in args.get("doc_ids", [])]
            payload = {
                "export_id": "EXP-" + hashlib.sha256("|".join(requested).encode()).hexdigest()[:8],
                "exported": [i for i in requested if i in allowed],
                "rejected": [i for i in requested if i not in allowed],
            }
        else:
            raise ValueError(f"tool desconocida: {name}")
        return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
