import logging

import structlog


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Provider URLs can contain private path credentials. HTTP transport INFO/DEBUG
    # records also bypass our structured business logging and must stay suppressed.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
