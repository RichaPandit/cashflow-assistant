# Cashflow Assistant

Cashflow Assistant is a Python Model Context Protocol (MCP) service that combines cashflow data from a Microsoft Fabric Lakehouse with exchange rates and supporting documents from Azure AI Search. Its tools return JSON suitable for an MCP client, including citations and an evidence trace for forecast results.

## Features

- Reads a Delta table from Microsoft Fabric OneLake using a configured ABFS path.
- Calculates a cashflow forecast in GBP and converts it to USD using ExchangeRate-API; a separate tool supports retrieving other currency pairs.
- Searches the `cashflow-rag` Azure AI Search index for supporting documents.
- Exposes MCP tools and a cashflow resource over Streamable HTTP at `/mcp`.
- Includes a GitHub Actions workflow for deployment to Azure App Service.

## How it works

For a forecast request, the service:

1. Reads the Delta table specified by `ABFS_PATH`.
2. Uses the `net_cashflow` column. If the table has a `month` column, it sums cashflow by month; otherwise, it averages the last three non-null values.
3. Fetches a GBP-to-USD exchange rate.
4. Searches Azure AI Search using the request's query and adds document results to the answer.
5. Returns the forecast, citations, monthly breakdown when available, and an `evidence_trace`.

The Delta table must contain `net_cashflow`. The optional `month` column enables a monthly breakdown. The Azure AI Search index is named `cashflow-rag`; the search code requests the `chunk`, `title`, `url`, and `parent_id` fields.

## MCP interface

The Streamable HTTP endpoint is:

```text
http://localhost:8000/mcp
```

The server currently exposes:

| Name | Description |
| --- | --- |
| `get_cashflow_forecast(query)` | Returns a cashflow forecast, FX conversion, supporting documents, citations, and evidence trace. |
| `search_documents_tool(query, top=3)` | Searches the document index and returns matching results as JSON. |
| `get_exchange_rate(base_currency="GBP", target_currency="USD")` | Returns the exchange rate for the requested currencies. |

It also exposes the `data://cashflow/fabric` resource with raw cashflow values.

### Forecast response

The forecast tool returns a JSON-encoded string with this general shape:

```json
{
  "answer": "Projected cash flow is £1200 (~$1500).",
  "forecast_gbp": 1200,
  "forecast_usd": 1500,
  "fx_rate": 1.25,
  "citations": [],
  "evidence_trace": {
    "data_sources": [],
    "evidence": {},
    "transformations": [],
    "assumptions": [],
    "validation_metadata": {}
  },
  "monthly_breakdown": {}
}
```

`monthly_breakdown` is included when the Fabric query returns monthly values. The numeric amounts above are illustrative. If an exception occurs during forecast processing, the tool returns a JSON object containing an `error` field.

## Requirements

- Python 3.11 (the version used by the included deployment workflow)
- Access to a Microsoft Fabric Lakehouse Delta table through OneLake
- An Azure AI Search service with a `cashflow-rag` index
- An ExchangeRate-API key

## Configuration

Create a `.env` file in the project root for local development. Do not commit real credentials:

```dotenv
TENANT_ID=your-azure-tenant-id
CLIENT_ID=your-service-principal-client-id
CLIENT_SECRET=your-service-principal-secret
ABFS_PATH=abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Tables/<table>

SEARCH_ENDPOINT=https://<search-service>.search.windows.net
SEARCH_KEY=your-azure-ai-search-key

EXCHANGE_API_KEY=your-exchangerate-api-key
FX_BASE_CURRENCY=GBP
# Optional; defaults to https://v6.exchangerate-api.com/v6
FX_API_BASE_URL=https://v6.exchangerate-api.com/v6

# Optional server settings
HOST=0.0.0.0
PORT=8000
```

Grant the configured service principal permission to read the OneLake data. Configure the same application settings in Azure App Service for deployed environments; the application loads `.env` for local development, but production secrets should be stored as App Service settings or in a managed secret store.

## Run locally

From the project root, create and activate a virtual environment, then install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the ASGI application:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

The MCP endpoint will be available at `http://localhost:8000/mcp`. Stop the server with `Ctrl+C`.

On macOS or Linux, create and activate the environment with:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## Deployment

The workflow in `.github/workflows/main_cashflow-web-app.yml` runs on pushes to `main` and on manual dispatch. It installs the requirements and deploys to the Azure Web App configured in the workflow.

Before deployment:

1. Configure the workflow's Azure publish-profile secret in the GitHub repository.
2. Confirm the workflow's `app-name` matches the intended Azure Web App.
3. Add the required environment variables as Azure App Service application settings.
4. Configure the App Service startup command to serve the ASGI app, for example:

   ```bash
   gunicorn --bind=0.0.0.0:8000 --worker-class uvicorn.workers.UvicornWorker main:app
   ```

## Security notes

The service currently allows cross-origin requests from any origin. An authentication middleware is defined in `main.py`, but it is not enabled; do not treat `LOCAL_TOKEN` or `MCP_DEV_ASSUME_KEY` as active endpoint protection. Before exposing the endpoint publicly, enable and verify authentication and restrict CORS to trusted clients. Keep all credentials out of source control.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | MCP tools, resource, HTTP application, and server entry point. |
| `fabric.py` | Reads and aggregates the Fabric Delta table. |
| `rag.py` | Queries the Azure AI Search index. |
| `external_api.py` | Retrieves currency conversion rates. |
| `config.py` | Loads environment-based configuration. |
| `requirements.txt` | Python dependencies. |
| `.github/workflows/main_cashflow-web-app.yml` | GitHub Actions deployment workflow. |
