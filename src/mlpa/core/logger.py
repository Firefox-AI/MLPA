import logging
import sys
from typing import Any, cast

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
        "fastapi",
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
        level=getattr(logging, env.LOGURU_LEVEL.upper(), logging.INFO),
    )
    _enable_httpx_logging()
    _enable_asyncpg_logging()

    if env.LOG_JSON:
        logger.remove()
        # Log to stdout for GKE compatibility
        logger.add(
            sys.stdout,
            level=env.LOGURU_LEVEL.upper(),
            serialize=True,
            backtrace=True,
            diagnose=True,
        )
    else:
        # Pretty, colorized output for local development
        logger.add(
            env.LOG_FILE,
            rotation=env.LOG_ROTATION,
            compression=env.LOG_COMPRESSION,
            level=env.LOGURU_LEVEL,
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


def _httpx_params_repr(params):
    return _truncate_mapping(params) if isinstance(params, dict) else _truncate(params)


def _httpx_json_repr(json_data):
    if isinstance(json_data, dict) and "messages" in json_data:
        json_data = dict(json_data)
        json_data["messages"] = "[...]"
    return (
        _truncate_mapping(json_data)
        if isinstance(json_data, dict)
        else _truncate(json_data)
    )


def _enable_httpx_logging():
    if not env.HTTPX_LOGGING:
        return

    def _build_wrapper(method_name, original):
        method_label = method_name.upper()

        async def _wrapper(self, *args, **kwargs):
            url = args[0] if args else kwargs.get("url")
            logger.opt(lazy=True).debug(
                "HTTPX {} request -> url={!r} params_repr={!r} json_repr={!r}",
                lambda: method_label,
                lambda: url,
                lambda: _httpx_params_repr(kwargs.get("params")),
                lambda: _httpx_json_repr(kwargs.get("json")),
            )
            try:
                response = await original(self, *args, **kwargs)
            except Exception as exc:
                # Include the exception type + repr: transport failures often
                # have an empty str(), so the bare URL alone was undiagnosable.
                logger.error(
                    f"HTTPX {method_name.upper()=} request failed for {url=}: "
                    f"{type(exc).__name__}: {exc!r}"
                )
                raise
            logger.opt(lazy=True).debug(
                "HTTPX {} response <- url={!r} status_code={}",
                lambda: method_label,
                lambda: url,
                lambda: response.status_code,
            )
            return response

        cast(Any, _wrapper).__mlpa_httpx_logging__ = True
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
        logger.opt(lazy=True).debug(
            "ASYNCPG execute -> query={!r} args={!r}",
            lambda: query,
            lambda: args,
        )
        try:
            result = await original_execute(self, query, *args, **kwargs)
        except Exception:
            logger.error(f"ASYNCPG execute failed -> {query=}")
            raise
        logger.opt(lazy=True).debug(
            "ASYNCPG execute <- query={!r} args={!r} result={!r}",
            lambda: query,
            lambda: args,
            lambda: result,
        )
        return result

    cast(Any, _execute_wrapper).__mlpa_asyncpg_logging__ = True
    asyncpg.connection.Connection.execute = cast(Any, _execute_wrapper)
