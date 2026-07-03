import os
import logging
import json
from typing import List

from fabric import query_fabric_cashflow
from rag import search_documents
from external_api import get_fx_rate

# FastMCP
from fastmcp.server import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.http import create_streamable_http_app
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

try:
    from fastmcp.server.exceptions import ToolError
except Exception:
    class ToolError(Exception):
        pass

# -----------------
# Config & constants
# -----------------
HEADER_NAME = "x-agent-key"
LOCAL_TOKEN: str = os.getenv("MCP_DEV_ASSUME_KEY", os.getenv("LOCAL_TOKEN", "")).strip()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------
# Auth middleware
# -----------------
class UserAuthMiddleware(Middleware):
    async def on_message(self, context: MiddlewareContext, call_next):
        # Allow initialization and tool discovery without auth
        method = getattr(context, 'method', None)
        if method in ['initialize', 'tools/list', 'resources/list']:
            logger.info(f"Allowing {method} without authentication")
            return await call_next(context)
        
        headers = get_http_headers()

        mcp_api_key = headers.get(HEADER_NAME) or headers.get("api-key")
        if not mcp_api_key:
            logger.warning("No authentication key provided")
            raise ToolError("Access denied: no key provided")

        if not mcp_api_key.startswith("Bearer "):
            logger.warning("Invalid token format in %s", HEADER_NAME)
            raise ToolError("Access denied: invalid token format")

        token = mcp_api_key.removeprefix("Bearer ").strip()
        expected = (LOCAL_TOKEN or "").strip()
        if not expected:
            raise ToolError("Access denied: server not configured")
        if token != expected:
            logger.warning("Invalid token provided")
            raise ToolError("Access denied: invalid token")

        logger.info("Authentication successful")
        return await call_next(context)

# -----------------
# FastMCP app
# -----------------
mcp = FastMCP(
    name="CashflowAgent",
)
mcp.add_middleware(UserAuthMiddleware())

# -----------------
# TOOLS
# -----------------

@mcp.tool()
def get_cashflow_forecast(query: str) -> str:
    """
    Get cashflow forecast from Fabric Lakehouse with FX conversion and supporting documents.
    Returns JSON with forecast, FX rate, and citations from Azure AI Search.
    
    Args:
        query: Search query for supporting documents
    """
    logger.info("get_cashflow_forecast called with query: %s", query)
    try:
        # 1. Fabric via ABFS
        logger.info("Querying Fabric Lakehouse via ABFS...")
        values = query_fabric_cashflow()
        logger.info("Fabric values: %s", values)

        # If values is a dict, treat as monthly breakdown
        if isinstance(values, dict):
            forecast = sum(values.values())
            breakdown = {k: {"gbp": v, "usd": round(v * get_fx_rate(), 2)} for k, v in values.items()}
        else:
            breakdown = None
            forecast = sum(values) / len(values) if values else 0

        # 2. FX API
        logger.info("Querying FX API...")
        fx_rate = get_fx_rate()
        logger.info("FX rate: %s", fx_rate)

        # 3. Azure AI Search (Blob PDFs)
        logger.info("Querying Azure AI Search for docs...")
        docs_rag = search_documents(query)
        logger.info("Docs RAG: %s", docs_rag)

        # 4. Build answer with citations
        answer = f"Projected cash flow is £{int(forecast)} (~${int(forecast * fx_rate)})."
        if breakdown:
            answer += "\n\nBreakdown by month:" + "".join([f"\n- {month}: £{int(val['gbp'])} (~${int(val['usd'])})" for month, val in breakdown.items()])

        # Add clickable PDF/document links if present
        doc_links = []
        for d in docs_rag:
            url = d.get("metadata_storage_path", "")
            title = d.get("metadata_storage_name", "Document")
            if url:
                doc_links.append(f"[{title}]({url})")
            else:
                doc_links.append(title)
        if doc_links:
            answer += "\n\nSupporting documents:" + "".join([f"\n- {link}" for link in doc_links])

        # 5. Citations
        citations = [
            {
                "title": "Fabric Lakehouse (ABFS)",
                "url": "https://app.fabric.microsoft.com/",
                "source": "Fabric"
            },
            {
                "title": "Exchange Rate API",
                "url": "https://api.exchangerate-api.com",
                "source": "API"
            }
        ]

        for d in docs_rag:
            # Build a clickable link for PDFs if possible
            url = d.get("metadata_storage_path", "")
            title = d.get("metadata_storage_name", "Document")
            if url and url.lower().endswith(".pdf"):
                link = f"[{title}]({url})"
            else:
                link = url or title
            cite_title = link
            citations.append({
                "title": cite_title,
                "url": url,
                "source": d.get("source", "Azure AI Search")
            })

        # 6. Format Output
        result = {
            "answer": answer,
            "forecast_gbp": int(forecast),
            "forecast_usd": int(forecast * fx_rate),
            "fx_rate": fx_rate,
            "citations": citations
        }
        if breakdown:
            result["monthly_breakdown"] = breakdown
        logger.info("Returning result: %s", result)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Error in get_cashflow_forecast: %s", e, exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)

@mcp.tool()
def search_documents_tool(query: str, top: int = 3) -> str:
    """
    Search SharePoint documents indexed in Azure AI Search.
    
    Args:
        query: Search query
        top: Maximum number of results (default: 3)
    """
    docs = search_documents(query, top)
    return json.dumps(docs, ensure_ascii=False)

@mcp.tool()
def get_exchange_rate(base_currency: str = "GBP", target_currency: str = "USD") -> str:
    """
    Get current exchange rate between two currencies.
    
    Args:
        base_currency: Base currency code (default: GBP)
        target_currency: Target currency code (default: USD)
    """
    rate = get_fx_rate(base_currency, target_currency)
    result = {
        "base": base_currency,
        "target": target_currency,
        "rate": rate
    }
    return json.dumps(result, ensure_ascii=False)

# -----------------
# RESOURCES
# -----------------

@mcp.resource("data://cashflow/fabric", name="FabricCashflow",
              description="Cashflow data from Microsoft Fabric Lakehouse",
              mime_type="application/json")
def res_fabric_cashflow() -> List[float]:
    """Returns raw cashflow values from Fabric"""
    return query_fabric_cashflow()

# --------------------------
# ASGI app & direct run
# --------------------------
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Create MCP ASGI app
_mcp_app = create_streamable_http_app(
    server=mcp,
    streamable_http_path="/",
)

# Extract lifespan from MCP app for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start MCP lifespan
    async with _mcp_app.router.lifespan_context(app):
        yield

async def health_check():
    """Health check endpoint for Azure App Service"""
    return {"status": "healthy", "service": "CashflowAgent"}

# Create FastAPI app with MCP lifespan
app = FastAPI(
    title="CashflowAgent",
    lifespan=lifespan
)

# Add health check endpoints
@app.get("/")
@app.get("/health")
async def health():
    return await health_check()

# Mount MCP app at /mcp
app.mount("/mcp", _mcp_app)

# Add CORS middleware for Copilot Studio
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port, path="/mcp")