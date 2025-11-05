import base64
import functools
import inspect
import tracemalloc

import httpx
from fastapi import HTTPException
from fxa.oauth import Client
from loguru import logger

from mlpa.core.config import LITELLM_HEADERS, env


async def get_or_create_user(user_id: str):
    """Returns user info from LiteLLM, creating the user if they don't exist.
    Args:
        user_id (str): The user ID to look up or create.
    Returns:
        [user_info: dict, was_created: bool]
    """

    async with httpx.AsyncClient() as client:
        try:
            params = {"end_user_id": user_id}
            response = await client.get(
                f"{env.LITELLM_API_BASE}/customer/info",
                params=params,
                headers=LITELLM_HEADERS,
            )
            user = response.json()
            if not user.get("user_id"):
                # add budget details or budget_id if necessary
                await client.post(
                    f"{env.LITELLM_API_BASE}/customer/new",
                    json={"user_id": user_id},
                    headers=LITELLM_HEADERS,
                )
                response = await client.get(
                    f"{env.LITELLM_API_BASE}/customer/info",
                    params=params,
                    headers=LITELLM_HEADERS,
                )
                return [response.json(), True]
            return [user, False]
        except Exception as e:
            logger.error(f"Error fetching or creating user {user_id}: {e}")
            raise HTTPException(
                status_code=500, detail={"error": f"Error fetching user info"}
            )


def b64decode_safe(data_b64: str, obj_name: str = "object") -> str:
    try:
        return base64.urlsafe_b64decode(data_b64)
    except Exception as e:
        logger.error(f"Error decoding base64 for {obj_name}: {e}")
        raise HTTPException(status_code=400, detail={obj_name: f"Invalid Base64"})


def get_fxa_client():
    fxa_url = (
        "https://api-accounts.stage.mozaws.net/v1"
        if env.MLPA_DEBUG
        else "https://oauth.accounts.firefox.com/v1"
    )
    return Client(env.CLIENT_ID, env.CLIENT_SECRET, fxa_url)


def profile_memory(func):
    """
    Decorator to profile memory usage of async functions and async generators.
    Only profiles if MEMORY_PROFILING is enabled in config.
    """
    if inspect.isasyncgenfunction(func):
        # Handle async generators
        @functools.wraps(func)
        async def async_gen_wrapper(*args, **kwargs):
            if not env.MEMORY_PROFILING:
                async for item in func(*args, **kwargs):
                    yield item
                return

            tracemalloc.start()
            snapshot_before = tracemalloc.take_snapshot()

            try:
                async for item in func(*args, **kwargs):
                    yield item

                # Take snapshot after generator is exhausted
                snapshot_after = tracemalloc.take_snapshot()
                top_stats = snapshot_after.compare_to(snapshot_before, "lineno")

                total_allocated = sum(
                    stat.size_diff for stat in top_stats if stat.size_diff > 0
                )
                total_freed = abs(
                    sum(stat.size_diff for stat in top_stats if stat.size_diff < 0)
                )
                net_change = total_allocated - total_freed

                logger.info(
                    f"Memory profile for {func.__name__}: "
                    f"allocated={total_allocated / 1024 / 1024:.2f} MB, "
                    f"freed={total_freed / 1024 / 1024:.2f} MB, "
                    f"net_change={net_change / 1024 / 1024:.2f} MB"
                )

                # Log top 5 allocations if significant
                if total_allocated > 1024 * 1024:  # Only if > 1MB allocated
                    logger.debug(f"Top memory allocations for {func.__name__}:")
                    for i, stat in enumerate(top_stats[:5], 1):
                        if stat.size_diff > 0:
                            logger.debug(
                                f"  {i}. {stat.size_diff / 1024 / 1024:.2f} MB - "
                                f"{stat.traceback.format()[-1] if stat.traceback else 'N/A'}"
                            )
            finally:
                tracemalloc.stop()

        return async_gen_wrapper
    else:
        # Handle regular async functions
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not env.MEMORY_PROFILING:
                return await func(*args, **kwargs)

            tracemalloc.start()
            snapshot_before = tracemalloc.take_snapshot()

            try:
                result = await func(*args, **kwargs)
                snapshot_after = tracemalloc.take_snapshot()

                top_stats = snapshot_after.compare_to(snapshot_before, "lineno")

                total_allocated = sum(
                    stat.size_diff for stat in top_stats if stat.size_diff > 0
                )
                total_freed = abs(
                    sum(stat.size_diff for stat in top_stats if stat.size_diff < 0)
                )
                net_change = total_allocated - total_freed

                logger.info(
                    f"Memory profile for {func.__name__}: "
                    f"allocated={total_allocated / 1024 / 1024:.2f} MB, "
                    f"freed={total_freed / 1024 / 1024:.2f} MB, "
                    f"net_change={net_change / 1024 / 1024:.2f} MB"
                )

                # Log top 5 allocations if significant
                if total_allocated > 1024 * 1024:  # Only if > 1MB allocated
                    logger.debug(f"Top memory allocations for {func.__name__}:")
                    for i, stat in enumerate(top_stats[:5], 1):
                        if stat.size_diff > 0:
                            logger.debug(
                                f"  {i}. {stat.size_diff / 1024 / 1024:.2f} MB - "
                                f"{stat.traceback.format()[-1] if stat.traceback else 'N/A'}"
                            )

                return result
            finally:
                tracemalloc.stop()

        return wrapper
