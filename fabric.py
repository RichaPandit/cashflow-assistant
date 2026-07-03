import logging
import pandas as pd
from deltalake import DeltaTable
from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET, ABFS_PATH

logger = logging.getLogger(__name__)


def query_fabric_cashflow():
    storage_options = {
        "azure_tenant_id": TENANT_ID,
        "azure_client_id": CLIENT_ID,
        "azure_client_secret": CLIENT_SECRET,
    }

    try:
        logger.info("Reading Delta table from OneLake path: %s", ABFS_PATH)
        dt = DeltaTable(ABFS_PATH, storage_options=storage_options)
        df = dt.to_pandas()
        logger.info("Delta table loaded. Shape: %s, Columns: %s", df.shape, df.columns.tolist())

        if "net_cashflow" not in df.columns:
            logger.error("Column 'net_cashflow' not found. Available: %s", df.columns.tolist())
            return []

        if "month" in df.columns:
            grouped = df.groupby("month")["net_cashflow"].sum().sort_index()
            logger.info("Monthly cashflow breakdown: %s", grouped.to_dict())
            return grouped.to_dict()
        else:
            values = df["net_cashflow"].dropna().tail(3).tolist()
            logger.info("Cashflow values: %s", values)
            return values

    except Exception as e:
        logger.error("Error reading Fabric Delta table via ABFS: %s", str(e), exc_info=True)
        return []
