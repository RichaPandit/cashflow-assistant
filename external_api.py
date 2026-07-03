import logging
import requests
from config import EXCHANGE_API_KEY as API_KEY, FX_BASE_CURRENCY as BASE_CURRENCY, FX_API_BASE_URL

logger = logging.getLogger(__name__)

def get_fx_rate(base_currency=None, target_currency="USD"):
    if base_currency is None:
        base_currency = BASE_CURRENCY

    if not API_KEY:
        logger.error("EXCHANGE_API_KEY is not set in environment variables")
        raise ValueError("EXCHANGE_API_KEY environment variable is not configured")

    url = f"{FX_API_BASE_URL}/{API_KEY}/latest/{base_currency}"
    logger.info("Fetching FX rate from: %s", url)
    res = requests.get(url, timeout=10).json()

    if res.get("result") == "error":
        logger.error("FX API error: %s", res.get("error-type"))
        raise ValueError(f"FX API error: {res.get('error-type')}")

    return res['conversion_rates'][target_currency]