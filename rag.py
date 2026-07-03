import logging
import requests
from config import SEARCH_ENDPOINT, SEARCH_KEY

logger = logging.getLogger(__name__)

def search_documents(query, top=3):
    """
    Query Azure Cognitive Search for documents indexed from Azure Blob Storage.
    """
    url = f"{SEARCH_ENDPOINT}/indexes/cashflow-rag/docs/search?api-version=2023-11-01"
    headers = {
        "api-key": SEARCH_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "search": query,
        "top": top,
        "select": "chunk,title,url,parent_id"
    }

    logger.info("Searching index at: %s | query: %s", url, query)

    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        logger.info("Search HTTP status: %s", res.status_code)

        if res.status_code != 200:
            logger.error("Search error response: %s", res.text)
            return []

        data = res.json()
        results = data.get("value", [])

        logger.info(
            "Search returned %s results. First result keys: %s",
            len(results),
            list(results[0].keys()) if results else []
        )

        normalised = []
        for r in results:
            normalised.append({
                # keep the same output key to avoid breaking callers
                "metadata_storage_name": (
                    r.get("title") or r.get("id", "Document")
                ),

                # canonical content field
                "content": (
                    r.get("content") or r.get("chunk") or ""
                ),

                # canonical citation field
                "metadata_storage_path": (
                    r.get("url") or ""
                ),

                "parent_id": r.get("parent_id")
            })

        return normalised

    except Exception as e:
        logger.error("Search request failed: %s", str(e), exc_info=True)
        return []