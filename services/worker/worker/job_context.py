from contextvars import ContextVar

# Background-Job-Aequivalent von app/core/request_context.py in services/api:
# kein HTTP-Request vorhanden, daher die natuerliche Korrelations-ID des
# Jobs selbst (z.B. import_run_id) - von worker/actors.py gesetzt, von
# worker/logging_config.py gelesen.
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
