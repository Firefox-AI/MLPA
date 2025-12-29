import httpx

from mlpa.core.config import env

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=env.HTTPX_CONNECT_TIMEOUT_SECONDS,
                read=env.HTTPX_READ_TIMEOUT_SECONDS,
                write=env.HTTPX_WRITE_TIMEOUT_SECONDS,
                pool=env.HTTPX_POOL_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(
                max_connections=env.HTTPX_MAX_CONNECTIONS,
                max_keepalive_connections=env.HTTPX_MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=env.HTTPX_KEEPALIVE_EXPIRY_SECONDS,
            ),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
