from typing import Any

import httpx

from mlpa.core.classes import AuthorizedRequestContext
from mlpa.core.config import LITELLM_COMPLETION_AUTH_HEADERS, LITELLM_EXA_SEARCH_URL
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.utils import raise_and_log


async def proxy_exa_search(
    authorized_request: AuthorizedRequestContext, body: dict[str, Any]
) -> dict[str, Any]:
    """Proxy an authenticated Exa search request to LiteLLM."""
    logger.debug(f"Starting Exa search proxy for user {authorized_request.user}")
    try:
        response = await get_http_client().post(
            LITELLM_EXA_SEARCH_URL,
            headers=LITELLM_COMPLETION_AUTH_HEADERS,
            json={
                **body,
                "user": authorized_request.user,
            },
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise_and_log(e)
    except Exception as e:
        raise_and_log(
            e, response_code=502, response_text_prefix="Failed to proxy request"
        )
