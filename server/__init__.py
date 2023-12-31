import logging

import structlog

structlog.configure(
    cache_logger_on_first_use=True,  # see https://www.structlog.org/en/stable/performance.html#performance
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.dev.ConsoleRenderer(sort_keys=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
)
