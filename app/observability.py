"""Allowlisted logs: raw URL, query, body, headers and exception text never enter output."""

import json
import logging


class SafeFilter(logging.Filter):
    def filter(self, record):
        event = {"event": "server", "level": record.levelname}
        if isinstance(record.msg, dict) and record.name == "miroven.requests":
            for key in ("request_id", "method", "route", "status"):
                if key in record.msg:
                    event[key] = record.msg[key]
            event["event"] = "request"
        if record.exc_info and record.exc_info[0]:
            event["error_type"] = record.exc_info[0].__name__
        record.msg = json.dumps(event)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"safe": {"()": "app.observability.SafeFilter"}},
    "handlers": {"safe": {"class": "logging.StreamHandler", "filters": ["safe"]}},
    "root": {"handlers": ["safe"], "level": "INFO"},
    "loggers": {
        "uvicorn": {"handlers": [], "propagate": True},
        "uvicorn.error": {"handlers": [], "propagate": True},
        "uvicorn.access": {"handlers": [], "propagate": False},
    },
}
