import os
import logging
import json
from typing import List

from fabric import query_fabric_cashflow
from rag import search_documents
from external_api import get_fx_rate

# FastMCP
from fastmcp import FastMCP
from starlette.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------
# FastMCP server
# -----------------
mcp = FastMCP("CashflowAgent")

# -----------------
# TOOLS
# -----------------

@mcp.tool()
async def get_cashflow_forecast(query: str) -> str:
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
async def search_documents_tool(query: str, top: int = 3) -> str:
    """
    Search SharePoint documents indexed in Azure AI Search.
    
    Args:
        query: Search query
        top: Maximum number of results (default: 3)
    """
    docs = search_documents(query, top)
    return json.dumps(docs, ensure_ascii=False)

@mcp.tool()
async def get_exchange_rate_tool(base_currency: str = "GBP", target_currency: str = "USD") -> str:
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
# Health check endpoint
# -----------------
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for Azure App Service"""
    return JSONResponse({"status": "healthy", "service": "CashflowAgent"})

# -----------------
# Stateless JSON-RPC endpoint for Copilot Studio
# -----------------
@mcp.custom_route("/mcp", methods=["GET", "POST"])
async def mcp_jsonrpc_endpoint(request):
    """Stateless JSON-RPC endpoint for Copilot Studio"""
    
    # Handle GET request (validation)
    if request.method == "GET":
        return JSONResponse({
            "jsonrpc": "2.0",
            "error": {"code": -32000, "message": "Method not allowed."},
            "id": None
        })
    
    # Handle POST request (JSON-RPC)
    try:
        body = await request.json()
        logger.info(f"MCP request: {json.dumps(body)}")
        
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id", 1)
        
        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "CashflowAgent", "version": "1.0.0"}
                }
            })
        
        elif method == "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "get_cashflow_forecast",
                            "description": "Get cashflow forecast from Fabric Lakehouse with FX conversion and supporting documents",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search query for supporting documents"}
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "search_documents_tool",
                            "description": "Search SharePoint documents indexed in Azure AI Search",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search query"},
                                    "top": {"type": "integer", "description": "Maximum number of results (default: 3)"}
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "get_exchange_rate_tool",
                            "description": "Get current exchange rate between two currencies",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "base_currency": {"type": "string", "description": "Base currency code (default: GBP)"},
                                    "target_currency": {"type": "string", "description": "Target currency code (default: USD)"}
                                }
                            }
                        }
                    ]
                }
            })
        
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            
            try:
                if tool_name == "get_cashflow_forecast":
                    result = await get_cashflow_forecast(**tool_args)
                elif tool_name == "search_documents_tool":
                    result = await search_documents_tool(**tool_args)
                elif tool_name == "get_exchange_rate_tool":
                    result = await get_exchange_rate_tool(**tool_args)
                else:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
                    })
                
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": result}]}
                })
            except Exception as e:
                logger.error(f"Tool execution error: {e}", exc_info=True)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": str(e)}
                })
        
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            })
            
    except Exception as e:
        logger.error(f"Request parsing error: {e}", exc_info=True)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"}
        })

# -----------------
# Create HTTP app and run
# -----------------
app = mcp.http_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)