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
from starlette.middleware.cors import CORSMiddleware

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
# Temporarily disable auth for testing tool discovery
# mcp.add_middleware(UserAuthMiddleware())

# -----------------
# TOOLS
# -----------------

@mcp.tool()

def get_cashflow_forecast(
    query: str,
    start_month: str | None = None,
    end_month: str | None = None,
) -> str:

    """
    Get cashflow forecast from Fabric Lakehouse with FX conversion and supporting documents.
    Returns:
        JSON containing the original forecast response plus an evidence trace
        for downstream Critic Council validation.
    Args:
        query: Search query for supporting documents
        start_month: Optional inclusive start date for the forecast range (YYYY-MM-DD)
        end_month: Optional inclusive end date for the forecast range (YYYY-MM-DD)
    """
    logger.info("get_cashflow_forecast called with query: %s", query)

    try:
        # ---------------------------------------------------------
        # 1. Fabric Lakehouse
        # ---------------------------------------------------------
        logger.info("Querying Fabric Lakehouse via ABFS...")
        values = query_fabric_cashflow(source="forecast", start_month=start_month, end_month=end_month)
        logger.info("FORECAST VALUES TYPE: %s", type(values))
        logger.info("FORECAST VALUES CONTENT: %s", values)
        logger.info("Fabric values: %s", values)

        # No data is NOT the same as a zero forecast.
        if not values:
            result = {
                "answer": (
                    "No cashflow forecast could be calculated because "
                    "the underlying Fabric Lakehouse query returned no records."
                ),
                "forecast_gbp": None,
                "forecast_usd": None,
                "fx_rate": None,
                "citations": [
                    {
                        "title": "Fabric Lakehouse (ABFS)",
                        "url": "https://app.fabric.microsoft.com/",
                        "source": "Fabric"
                    }
                ],
                "evidence_trace": {
                    "data_sources": [
                        {
                            "source": "Cashflow_Lakehouse",
                            "type": "fabric",
                            "tables": [
                                "balances",
                                "cashflow_table",
                                "cashflow_forecast"
                            ]
                        }
                    ],
                    "evidence": {
                        "fabric_result": [],
                        "document_results": []
                    },
                    "transformations": [],
                    "assumptions": [],
                    "validation_metadata": {
                        "forecast_status": "NO_DATA",
                        "forecast_currency": "GBP",
                        "query": query,
                        "monthly_breakdown_available": False
                    }
                }
            }

            logger.info("No Fabric data returned. Returning NO_DATA response.")

            return json.dumps(
                result,
                ensure_ascii=False
            )

        # If values is a dict, treat it as a monthly breakdown.
        if isinstance(values, dict):

            forecast = sum(values.values())
            # Get FX rate once and use the same rate consistently.
            fx_rate = get_fx_rate()
            breakdown = {
                k: {
                    "gbp": v,
                    "usd": round(v * fx_rate, 2)
                }
                for k, v in values.items()
            }
        else:
            breakdown = None
            forecast = sum(values) / len(values) if values else 0

            # ---------------------------------------------------------
            # 2. FX API
            # ---------------------------------------------------------
            logger.info("Querying FX API...")
            fx_rate = get_fx_rate()
            logger.info("FX rate: %s", fx_rate)

        # ---------------------------------------------------------
        # 3. Calculate USD result
        # ---------------------------------------------------------
        forecast_usd = round(forecast * fx_rate, 2)

        # ---------------------------------------------------------
        # 4. Azure AI Search / RAG
        # ---------------------------------------------------------
        logger.info("Querying Azure AI Search for docs...")
        docs_rag = search_documents(query)
        logger.info("Docs RAG: %s", docs_rag)

        # ---------------------------------------------------------
        # 5. Build answer
        # ---------------------------------------------------------
        answer = (
            f"Projected cash flow is £{int(forecast)} "
            f"(~${int(forecast_usd)})."
        )

        if breakdown:
            answer += "\n\nBreakdown by month:"
            answer += "".join(
                [
                    f"\n- {month}: £{int(val['gbp'])} "
                    f"(~${int(val['usd'])})"
                    for month, val in breakdown.items()
                ]
            )

        # ---------------------------------------------------------
        # 6. Supporting document links
        # ---------------------------------------------------------
        doc_links = []

        for d in docs_rag:
            url = d.get("metadata_storage_path", "")
            title = d.get("metadata_storage_name", "Document")
            if url:
                doc_links.append(f"[{title}]({url})")
            else:
                doc_links.append(title)

        if doc_links:
            answer += "\n\nSupporting documents:"
            answer += "".join(
                [f"\n- {link}" for link in doc_links]
            )

        # ---------------------------------------------------------
        # 7. Citations
        # ---------------------------------------------------------
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
            url = d.get("metadata_storage_path", "")
            page = d.get("page")
            title = d.get("metadata_storage_name", "Document")

            if url and url.lower().endswith(".pdf"):
                link = f"[{title}]({url})"
            else:
                link = url or title

            if page:
                cite_title = f"{link} (page {page})"
            else:
                cite_title = link

            citations.append(
                {
                    "title": cite_title,
                    "url": url,
                    "source": d.get(
                        "source",
                        "Azure AI Search"
                    )
                }
            )

        # =========================================================
        # 8. CRITIC COUNCIL EVIDENCE TRACE
        # =========================================================

        evidence_trace = {
            "data_sources": [
                {
                    "source": "Cashflow_Lakehouse",
                    "type": "fabric",
                    "tables": [
                        "balances",
                        "cashflow_table",
                        "cashflow_forecast"
                    ]
                },
                {
                    "source": "Exchange Rate API",
                    "type": "fx_api",
                    "base_currency": "GBP",
                    "target_currency": "USD",
                    "rate": fx_rate
                },
                {
                    "source": "Azure AI Search",
                    "type": "document_search",
                    "result_count": len(docs_rag)
                }
            ],
            "evidence": {
                "fabric_result": values,
                "document_results": docs_rag
            },
            "transformations": [
                {
                    "operation": "forecast_calculation",
                    "input": values,
                    "result": forecast,
                    "method": (
                        "sum(monthly_values)"
                        if isinstance(values, dict)
                        else "average(returned_values)"
                    )
                },
                {
                    "operation": "currency_conversion",
                    "source_currency": "GBP",
                    "target_currency": "USD",
                    "input_value": forecast,
                    "fx_rate": fx_rate,
                    "formula": "GBP_amount * GBP_USD_rate",
                    "result": forecast_usd
                }
            ],
            "assumptions": [],
            "validation_metadata": {
                "forecast_currency": "GBP",
                "target_currency": "USD",
                "query": query,
                "monthly_breakdown_available": breakdown is not None
            }
        }

        # ---------------------------------------------------------
        # 9. Preserve existing response contract + add trace
        # ---------------------------------------------------------
        result = {
            "answer": answer,
            "forecast_gbp": int(forecast),
            "forecast_usd": int(forecast_usd),
            "fx_rate": fx_rate,
            "citations": citations,
            # New Critic Council payload
            "evidence_trace": evidence_trace
        }

        if breakdown:
            result["monthly_breakdown"] = breakdown

        logger.info("Returning result: %s", result)

        return json.dumps(
            result,
            ensure_ascii=False
        )

    except Exception as e:
        logger.error(
            "Error in get_cashflow_forecast: %s",
            e,
            exc_info=True
        )

        return json.dumps(
            {"error": str(e)},
            ensure_ascii=False
        )

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
_mcp_app = create_streamable_http_app(
    server=mcp,
    streamable_http_path="/mcp",
)

# Wrap with CORS so Copilot Studio (cross-origin) can reach the MCP endpoint
app = CORSMiddleware(
    app=_mcp_app,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port, path="/mcp")
