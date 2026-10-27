"""Flask integration: ``TrutiFlask(app)`` or ``init_app(app)``."""
import truti


def init_app(app):
    from flask import got_request_exception, request

    @app.before_request
    def _truti_before():
        try:
            truti.add_breadcrumb(f"{request.method} {request.path}", category="http")
            s = truti.scope.get_scope()
            s.request = {"method": request.method, "url": request.path,
                         "headers": {k: v for k, v in request.headers.items()}}
        except Exception:
            pass

    def _on_exc(sender, exception, **extra):
        truti.capture_exception(exception)

    got_request_exception.connect(_on_exc, app, weak=False)
    return app


TrutiFlask = init_app
