"""Pure-ASGI middleware (FastAPI, Starlette, Quart, ...)."""
import truti
from truti.scope import push_scope

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}


class TrutiASGIMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        with push_scope() as s:
            try:
                headers = {k.decode("latin-1"): ("[Filtered]" if k.decode("latin-1").lower() in _SENSITIVE_HEADERS
                                                 else v.decode("latin-1")) for k, v in scope.get("headers", [])}
                s.request = {"method": scope.get("method", "WS"), "url": scope.get("path", ""),
                             "query_string": scope.get("query_string", b"").decode("latin-1"),
                             "headers": headers}
                truti.add_breadcrumb(f"{s.request['method']} {s.request['url']}", category="http")
            except Exception:
                pass
            try:
                await self.app(scope, receive, send)
            except Exception as exc:
                truti.capture_exception(exc)
                raise
