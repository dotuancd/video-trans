# W3C trace context ids: one trace per job, one span per attempt, so logs can go to an OpenTelemetry
# backend later without changing ids.
import secrets


def new_trace_id():
    return secrets.token_hex(16)


def new_span_id():
    return secrets.token_hex(8)


def error_code(span_id):
    """The short code shown to the user next to a system error; the admin finds the full log by it."""
    return span_id[:8]
