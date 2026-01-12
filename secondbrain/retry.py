"""
Retry Decorator with Exponential Backoff for SecondBrain

Provides robust retry logic for API calls with configurable:
- Max retries
- Exponential backoff with jitter
- Retryable exception types
- Callbacks for retry events
"""

import functools
import random
import time
from typing import Callable, Optional, Tuple, Type, TypeVar, Any

from secondbrain.logger import get_logger

logger = get_logger(__name__)

# Type variable for return type preservation
T = TypeVar('T')


class RetryError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, message: str, last_exception: Exception, attempts: int):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int, float], None]] = None,
) -> Callable:
    """
    Decorator that retries a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay between retries (default: 60.0)
        exponential_base: Base for exponential calculation (default: 2.0)
        jitter: Add random jitter to prevent thundering herd (default: True)
        retryable_exceptions: Tuple of exceptions to retry on (default: all)
        on_retry: Optional callback(exception, attempt, delay) called before each retry

    Returns:
        Decorated function with retry logic

    Usage:
        @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
        def fetch_data():
            return requests.get(url)

        # Or with custom settings:
        @retry_with_backoff(max_retries=5, base_delay=0.5, max_delay=30)
        def api_call():
            return call_api()
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as e:
                    last_exception = e

                    # If this was the last attempt, raise
                    if attempt >= max_retries:
                        logger.error(
                            f"All {max_retries + 1} attempts failed for {func.__name__}",
                            extra={
                                "function": func.__name__,
                                "attempts": attempt + 1,
                                "error": str(e),
                                "error_type": type(e).__name__
                            }
                        )
                        raise RetryError(
                            f"Failed after {attempt + 1} attempts: {str(e)}",
                            last_exception=e,
                            attempts=attempt + 1
                        ) from e

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)

                    # Add jitter to prevent thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random())

                    logger.warning(
                        f"Attempt {attempt + 1} failed for {func.__name__}, retrying in {delay:.2f}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "delay": round(delay, 2),
                            "error": str(e),
                            "error_type": type(e).__name__
                        }
                    )

                    # Call retry callback if provided
                    if on_retry:
                        on_retry(e, attempt + 1, delay)

                    time.sleep(delay)

            # Should never reach here, but just in case
            raise RetryError(
                f"Unexpected failure after {max_retries + 1} attempts",
                last_exception=last_exception or Exception("Unknown error"),
                attempts=max_retries + 1
            )

        return wrapper

    return decorator


def retry_on_rate_limit(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
) -> Callable:
    """
    Specialized retry decorator for rate-limited APIs (like Notion).

    Uses longer delays and more retries suitable for rate limit handling.
    """
    import requests

    return retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=(
            requests.exceptions.RequestException,
            requests.exceptions.HTTPError,
            ConnectionError,
            TimeoutError,
        ),
    )


def retry_on_network_error(
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Callable:
    """
    Specialized retry decorator for transient network errors.
    """
    import requests
    import ssl

    return retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        retryable_exceptions=(
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            ConnectionError,
            TimeoutError,
            ssl.SSLError,
        ),
    )


class RetryContext:
    """
    Context manager for retry logic when decorators aren't suitable.

    Usage:
        with RetryContext(max_retries=3) as ctx:
            for attempt in ctx:
                try:
                    result = risky_operation()
                    break
                except SomeError as e:
                    ctx.handle_error(e)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.attempt = 0
        self.last_exception: Optional[Exception] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __iter__(self):
        for i in range(self.max_retries + 1):
            self.attempt = i
            yield i

    def handle_error(self, exception: Exception) -> None:
        """Handle an error and sleep before next attempt."""
        self.last_exception = exception

        if self.attempt >= self.max_retries:
            raise RetryError(
                f"Failed after {self.attempt + 1} attempts",
                last_exception=exception,
                attempts=self.attempt + 1
            ) from exception

        delay = min(
            self.base_delay * (self.exponential_base ** self.attempt),
            self.max_delay
        )

        if self.jitter:
            delay = delay * (0.5 + random.random())

        logger.warning(
            f"Attempt {self.attempt + 1} failed, retrying in {delay:.2f}s",
            extra={
                "attempt": self.attempt + 1,
                "delay": round(delay, 2),
                "error": str(exception)
            }
        )

        time.sleep(delay)
