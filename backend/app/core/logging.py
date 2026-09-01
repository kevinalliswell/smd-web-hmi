"""结构化日志配置（structlog）。"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", *, production: bool = False) -> None:
    """初始化 structlog + 标准库 logging，输出带时间戳的结构化日志。"""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    renderer = structlog.processors.JSONRenderer() if production else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """返回绑定名字的结构化 logger。"""
    return structlog.get_logger(name)
