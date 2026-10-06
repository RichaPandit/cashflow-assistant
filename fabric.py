import logging
import pandas as pd
from deltalake import DeltaTable
from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET, CASHFLOW_TABLE_PATH, BALANCES_TABLE_PATH, CASHFLOW_FORECAST_TABLE_PATH

logger = logging.getLogger(__name__)

def query_fabric_cashflow(source="forecast", start_month=None, end_month=None):
    storage_options = {
        "azure_tenant_id": TENANT_ID,
        "azure_client_id": CLIENT_ID,
        "azure_client_secret": CLIENT_SECRET,
    }

    try:
        if source == "forecast":
            path = CASHFLOW_FORECAST_TABLE_PATH  # Use the forecast table path
        elif source == "historical":
            path = CASHFLOW_TABLE_PATH  # Use the historical table path
        elif source == "balances":
            path = BALANCES_TABLE_PATH  # Use the balances table path
        else:
            logger.error("Invalid source specified: %s", source)
            return []
        logger.info("Reading Delta table from OneLake path: %s", path)
        dt = DeltaTable(path, storage_options=storage_options)
        df = dt.to_pandas()
        df.columns = [str(c).strip().lower() for c in df.columns]  # Normalize column names to lowercase
        logger.info("Delta table loaded. Shape: %s, Columns: %s", df.shape, df.columns.tolist())

        if "net_cashflow" not in df.columns:
            logger.error("Column 'net_cashflow' not found. Available: %s", df.columns.tolist())
            return []

        if "month" in df.columns:
            df["month"] = pd.to_datetime(df["month"])
            if start_month and end_month:
                df = df[
                    (df["month"] >= pd.Timestamp(start_month)) &
                    (df["month"] <= pd.to_datetime(end_month))
                ]
            grouped = df.groupby("month")["net_cashflow"].sum().sort_index()
            grouped.index = grouped.index.strftime("%Y-%m-%d")
            values = grouped.to_dict()
            logger.info("Monthly cashflow breakdown: %s", values)
            return values
        else:
            values = df["net_cashflow"].dropna().tail(3).tolist()
            logger.info("Cashflow values: %s", values)
            return values

    except Exception as e:
        logger.error("Error reading Fabric Delta table via ABFS: %s", str(e), exc_info=True)
        return []
