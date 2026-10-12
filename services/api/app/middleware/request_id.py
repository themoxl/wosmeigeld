import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import request_id_var

_REQUEST_ID_HEADER = b"x-request-id"


class RequestIdMiddleware:
    """Reine ASGI-Middleware statt starlette.middleware.base.BaseHTTPMiddleware:
    Letztere fuehrt den nachgelagerten Call-Stack in einem eigenen anyio-Task
    aus, was die Propagation von ContextVars (siehe app/core/request_context.py)
    in Requesthandler hinein je nach Starlette-/AnyIO-Version brechen kann.
    Eine rohe ASGI-Middleware vermeidet das vollstaendig.

    Uebernimmt einen vom Client/Reverse-Proxy gesetzten X-Request-Id-Header,
    sonst wird eine neue ID erzeugt. Die ID landet automatisch in jeder
    JSON-Logzeile des Requests (app/core/logging.py) und im
    X-Request-Id-Response-Header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (value for key, value in scope.get("headers", []) if key == _REQUEST_ID_HEADER),
            None,
        )
        request_id = incoming.decode("latin-1") if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_REQUEST_ID_HEADER, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)
