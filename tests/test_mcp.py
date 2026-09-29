import pytest

from app.adapters.outbound.mcp_client import MCPDocumentClient, MCPError
from app.adapters.outbound.mcp_server_mock import MockMCPServer
from app.adapters.outbound.mock_data import DOCUMENTS


@pytest.fixture
def mcp() -> MCPDocumentClient:
    return MCPDocumentClient(transport=MockMCPServer(DOCUMENTS).handle)


def test_metadata_filters_apply_before_ranking(mcp: MCPDocumentClient) -> None:
    # La query apunta a documentos de RRHH y RESTRINGIDOS; aun así no deben aparecer.
    docs = mcp.search(
        "escala salarial comité casos corporativos en mora crédito",
        area_filter="creditos",
        classification_filter=["publico", "interno"],
        top_k=10,
    )
    assert docs, "debería devolver documentos de créditos"
    assert all(d.area == "creditos" for d in docs)
    assert all(d.classification in {"publico", "interno"} for d in docs)
    assert "DOC-CRE-003" not in {d.doc_id for d in docs}


def test_server_fails_closed_without_filters(mcp: MCPDocumentClient) -> None:
    with pytest.raises(MCPError):
        mcp.search("crédito", area_filter="", classification_filter=[], top_k=4)


def test_search_returns_suspicious_provider_document(mcp: MCPDocumentClient) -> None:
    docs = mcp.search("requisitos buró crédito", "creditos", ["publico", "interno"], 4)
    prov = next(d for d in docs if d.doc_id == "DOC-PROV-007")
    assert "Ignora las reglas anteriores" in prov.content  # el MCP entrega el texto crudo
    assert prov.source == "proveedor_externo"
