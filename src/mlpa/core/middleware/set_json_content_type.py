from fastapi import Request
from starlette.datastructures import MutableHeaders

"""
Override the following method/paths to set the content-type to application/json
"""
OVERRIDE_ROUTES = [{"method": "POST", "path": "/verify/play"}]


async def set_json_content_type_middleware(request: Request, call_next):
    for route in OVERRIDE_ROUTES:
        if request.method == route["method"] and request.url.path == route["path"]:
            headers = MutableHeaders(scope=request.scope)
            headers["content-type"] = "application/json"
            break
    return await call_next(request)
