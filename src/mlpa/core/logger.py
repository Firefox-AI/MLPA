import logging

import asyncpg
import httpx
from loguru import logger

from mlpa.core.config import env

# Remove existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logger():
    loggers = (
        "alembic",
        "asyncio",
        "asyncpg",
        "fastapi",
        "httpx",
        "prometheus_client",
        "sentry_sdk",
        "sqlalchemy",
        "sqlalchemy.engine",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.asgi",
        "uvicorn.lifespan",
        "uvicorn.server",
        "uvicorn.protocols.http",
        "uvicorn.protocols.websockets",
        "uvicorn.error",
    )

    for logger_name in loggers:
        logging_logger = logging.getLogger(logger_name)
        logging_logger.handlers = []
        logging_logger.propagate = True

    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=getattr(logging, env.LOG_LEVEL.upper(), logging.INFO),
    )
    _enable_httpx_logging()
    _enable_asyncpg_logging()
    if env.LOG_FILE:
        logger.add(
            env.LOG_FILE,
            rotation=env.LOG_ROTATION,
            compression=env.LOG_COMPRESSION,
            level=env.LOG_LEVEL,
            backtrace=True,
            diagnose=True,
        )


def _truncate(value, limit=200):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _truncate_mapping(mapping, limit=5):
    if not mapping:
        return {}
    truncated_items = list(mapping.items())[:limit]
    result = {key: _truncate(value) for key, value in truncated_items}
    if len(mapping) > limit:
        result["..."] = "..."
    return result


def _enable_httpx_logging():
    if not env.HTTPX_LOGGING:
        return

    def _build_wrapper(method_name, original):
        async def _wrapper(self, *args, **kwargs):
            url = args[0] if args else kwargs.get("url")
            params = kwargs.get("params")
            json_data = kwargs.get("json")
            if isinstance(json_data, dict) and "messages" in json_data:
                json_data = dict(json_data)
                json_data["messages"] = "[...]"
            params_repr = (
                _truncate_mapping(params)
                if isinstance(params, dict)
                else _truncate(params)
            )
            json_repr = (
                _truncate_mapping(json_data)
                if isinstance(json_data, dict)
                else _truncate(json_data)
            )
            logger.debug(
                f"HTTPX {method_name.upper()} request -> {url=} {params_repr=} {json_repr=}"
            )
            try:
                response = await original(self, *args, **kwargs)
            except Exception:
                logger.error(f"HTTPX {method_name.upper()=} request failed for {url=}")
                raise
            logger.debug(
                f"HTTPX {method_name.upper()} response <- {url=} {response.status_code=}",
            )
            return response

        _wrapper.__mlpa_httpx_logging__ = True
        return _wrapper

    for method_name in ("get", "post"):
        original_method = getattr(httpx.AsyncClient, method_name)
        if getattr(original_method, "__mlpa_httpx_logging__", False):
            continue
        setattr(
            httpx.AsyncClient,
            method_name,
            _build_wrapper(method_name, original_method),
        )


def _enable_asyncpg_logging():
    if not env.ASYNCPG_LOGGING:
        return

    original_execute = asyncpg.connection.Connection.execute
    if getattr(original_execute, "__mlpa_asyncpg_logging__", False):
        return

    async def _execute_wrapper(self, query, *args, **kwargs):
        logger.debug(
            f"ASYNCPG execute -> {query=} {args=} {kwargs=}",
        )
        try:
            result = await original_execute(self, query, *args, **kwargs)
        except Exception:
            logger.error(f"ASYNCPG execute failed -> {query=}")
            raise
        logger.debug(
            f"ASYNCPG execute <- {query=} {result=}",
        )
        return result

    _execute_wrapper.__mlpa_asyncpg_logging__ = True
    asyncpg.connection.Connection.execute = _execute_wrapper
