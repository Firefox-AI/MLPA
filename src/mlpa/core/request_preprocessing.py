"""Opt-in body reading and JSON parsing timings at the FastAPI boundary."""

from typing import Any

from fastapi.routing import APIRoute
from starlette.requests import Request

from mlpa.core.request_timings import measure, timing_enabled


class TimingRequest(Request):
    async def body(self) -> bytes:
        if hasattr(self, "_body"):
            return await super().body()
        with measure("request_body_read", self):
            return await super().body()

    async def json(self) -> Any:
        if hasattr(self, "_json"):
            return await super().json()
        # Keep upload waiting out of JSON parsing time.
        await self.body()
        with measure("prompt_parsing", self):
            return await super().json()


class TimingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def timed_handler(request: Request):
            if not timing_enabled(request):
                return await handler(request)
            return await handler(TimingRequest(request.scope, request.receive))

        return timed_handler
