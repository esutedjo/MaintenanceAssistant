import re
import ssl
import uuid
from typing import Any

import httpx
import pandas as pd
import truststore
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from assurance.sql_guard import guard_select
from settings import Settings

RUN_QUERY_PATH = "/api/proxy/sql-editor/sqls/run-query"


class ZetarisError(RuntimeError):
    pass


def rows_to_frame(result: dict[str, Any]) -> pd.DataFrame:
    """Zetaris returns every value as a string; callers convert the columns they use."""
    return pd.DataFrame(result.get("data") or [], columns=result.get("headers") or None)


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500


class ZetarisClient:
    """Read-only SQL over the Zetaris AI Data Gateway UI proxy, authenticated with an API token."""

    def __init__(self, settings: Settings, timeout_s: float = 30.0):
        base_url = settings.zetaris_base_url.rstrip("/")
        if not re.fullmatch(r"https?://[^/]+", base_url):
            raise ZetarisError("ZETARIS_BASE_URL must be an http(s) origin with no path.")
        if not re.fullmatch(r"[0-9]+", settings.zetaris_org_id):
            raise ZetarisError("ZETARIS_ORG_ID must be the numeric organisation ID.")
        if not settings.zetaris_api_key.get_secret_value():
            raise ZetarisError("ZETARIS_API_KEY is not set.")

        self._limit = settings.zetaris_query_limit
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            headers={
                "Authorization": f"Bearer {settings.zetaris_api_key.get_secret_value()}",
                "X-Org-ID": settings.zetaris_org_id,
            },
        )

    def run_select(self, sql: str) -> Any:
        safe_sql = guard_select(sql, self._limit)
        return self._post(RUN_QUERY_PATH, {"sql": safe_sql, "limit": self._limit})

    def register_tables(self, datasource: str) -> Any:
        """Refresh Zetaris metadata after tables change in a source. Setup only; agents never call this."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", datasource):
            raise ZetarisError(f"Invalid data source name: {datasource!r}")
        return self._post(RUN_QUERY_PATH, {"sql": f"REGISTER DATASOURCE TABLES FROM {datasource}", "limit": 1})

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=2),
        reraise=True,
    )
    def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = self._http.post(path, json=body, headers={"X-Request-ID": str(uuid.uuid4())})
        if "text/html" in response.headers.get("content-type", ""):
            raise ZetarisError(f"POST {path} returned an HTML page; check ZETARIS_BASE_URL.")
        if response.status_code in (401, 403):
            raise ZetarisError(f"POST {path} was refused (HTTP {response.status_code}); check the API token and org ID.")
        if 400 <= response.status_code < 500:
            raise ZetarisError(f"POST {path} failed (HTTP {response.status_code}): {response.text[:500]}")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._http.close()
