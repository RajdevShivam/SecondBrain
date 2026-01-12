"""
Structured Logging for SecondBrain

Provides JSON-formatted logging for easy parsing in GitHub Actions
and other CI/CD environments.
"""

import logging
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class StructuredFormatter(logging.Formatter):
    """
    Formats log records as JSON for structured logging.

    Output format:
    {"timestamp": "2024-01-15T10:30:00Z", "level": "INFO", "module": "master_sync",
     "function": "process_inbox", "message": "Processing 5 items", "extra": {...}}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        # Add any extra fields passed via the 'extra' parameter
        if hasattr(record, 'extra') and record.extra:
            log_entry["extra"] = record.extra

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class PrettyFormatter(logging.Formatter):
    """
    Human-readable formatter for local development.

    Output format:
    [2024-01-15 10:30:00] INFO     master_sync.process_inbox: Processing 5 items
    """

    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        color = self.COLORS.get(record.levelname, '')
        reset = self.RESET if color else ''

        base = f"[{timestamp}] {color}{record.levelname:8}{reset} {record.module}.{record.funcName}: {record.getMessage()}"

        # Add extra fields if present
        if hasattr(record, 'extra') and record.extra:
            extra_str = ' '.join(f"{k}={v}" for k, v in record.extra.items())
            base += f" ({extra_str})"

        return base


class LoggerAdapter(logging.LoggerAdapter):
    """
    Custom adapter that makes it easy to add extra fields to log messages.

    Usage:
        logger = get_logger(__name__)
        logger.info("Processing page", extra={"page_id": "abc123", "status": "new"})
    """

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        extra = kwargs.get('extra', {})
        if self.extra:
            extra = {**self.extra, **extra}
        kwargs['extra'] = extra

        # Store extra in record for formatters
        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        return msg, kwargs


def get_logger(
    name: str,
    level: int = logging.INFO,
    json_output: bool = False,
    extra: Optional[Dict[str, Any]] = None
) -> LoggerAdapter:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)
        level: Logging level (default: INFO)
        json_output: If True, output JSON format; otherwise, pretty format
        extra: Default extra fields to include in all log messages

    Returns:
        LoggerAdapter instance with structured logging support

    Usage:
        from secondbrain.logger import get_logger

        logger = get_logger(__name__)
        logger.info("Starting sync")
        logger.info("Processing", extra={"page_id": "123", "count": 5})
        logger.error("Failed", extra={"error": str(e)})
    """
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times
    if not logger.handlers:
        logger.setLevel(level)

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        # Use JSON in CI/GitHub Actions, pretty format locally
        if json_output or _is_ci_environment():
            handler.setFormatter(StructuredFormatter())
        else:
            handler.setFormatter(PrettyFormatter())

        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

    return LoggerAdapter(logger, extra or {})


def _is_ci_environment() -> bool:
    """Detect if running in a CI environment."""
    import os
    ci_vars = ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL', 'CIRCLECI']
    return any(os.getenv(var) for var in ci_vars)


# Convenience functions for quick logging
_default_logger: Optional[LoggerAdapter] = None


def _get_default_logger() -> LoggerAdapter:
    """Get or create the default logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger("secondbrain")
    return _default_logger


def info(msg: str, **kwargs) -> None:
    """Log an info message."""
    _get_default_logger().info(msg, extra=kwargs if kwargs else None)


def warning(msg: str, **kwargs) -> None:
    """Log a warning message."""
    _get_default_logger().warning(msg, extra=kwargs if kwargs else None)


def error(msg: str, **kwargs) -> None:
    """Log an error message."""
    _get_default_logger().error(msg, extra=kwargs if kwargs else None)


def debug(msg: str, **kwargs) -> None:
    """Log a debug message."""
    _get_default_logger().debug(msg, extra=kwargs if kwargs else None)
