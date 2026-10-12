from contextvars import ContextVar

# Von app/middleware/request_id.py gesetzt, von app/core/logging.py gelesen -
# so landet die Request-ID automatisch in jeder JSON-Logzeile eines Requests,
# ohne sie durch jede Funktionssignatur durchreichen zu muessen.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
