"""
Telegram Client for SecondBrain

Provides messaging utilities for sending notifications via Telegram with:
- Markdown to HTML conversion
- Message splitting for long texts
- Automatic fallback to plain text
- Retry logic
"""

import re
import time
from typing import List, Optional

import requests

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.retry import retry_with_backoff

logger = get_logger(__name__)

# Telegram message length limit
MAX_MESSAGE_LENGTH = 4000


class TelegramClient:
    """
    Wrapper for Telegram Bot API operations.

    Usage:
        from secondbrain.telegram_client import TelegramClient

        client = TelegramClient()
        client.send_message("Hello, World!")

        # Or with long text:
        client.send_message(long_text)  # Auto-splits if needed
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ):
        """
        Initialize the Telegram client.

        Args:
            bot_token: Telegram bot token (defaults to settings.telegram_bot_token)
            chat_id: Default chat ID (defaults to settings.telegram_chat_id)
        """
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    @property
    def api_url(self) -> str:
        """Get the Telegram API base URL."""
        return f"https://api.telegram.org/bot{self.bot_token}"

    def _validate_credentials(self) -> bool:
        """Check if credentials are configured."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram credentials missing")
            return False
        return True

    @staticmethod
    def markdown_to_html(text: str) -> str:
        """
        Convert Markdown-style formatting to Telegram HTML.

        Handles:
        - **bold** and *bold* -> <b>bold</b>
        - _italic_ -> <i>italic</i>
        - Escapes HTML special characters

        Args:
            text: Text with Markdown formatting

        Returns:
            Text with Telegram HTML formatting
        """
        # Escape special HTML chars first (order matters!)
        clean_text = text.replace("&", "&amp;")
        clean_text = clean_text.replace("<", "&lt;")
        clean_text = clean_text.replace(">", "&gt;")

        # Convert **bold** to <b>bold</b>
        clean_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_text)

        # Convert *bold* to <b>bold</b> (word boundary aware)
        clean_text = re.sub(r'(?<!\w)\*(.*?)\*(?!\w)', r'<b>\1</b>', clean_text)

        # Convert _italic_ to <i>italic</i> (word boundary aware)
        clean_text = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'<i>\1</i>', clean_text)

        return clean_text

    @staticmethod
    def split_message(
        text: str,
        max_length: int = MAX_MESSAGE_LENGTH
    ) -> List[str]:
        """
        Split a long message into parts that fit Telegram's limit.

        Tries to split at paragraph boundaries, then line boundaries,
        then falls back to hard split.

        Args:
            text: Text to split
            max_length: Maximum length per part

        Returns:
            List of message parts
        """
        parts = []
        remaining = text

        while remaining:
            if len(remaining) <= max_length:
                parts.append(remaining)
                break

            # Try to find a good split point
            split_index = remaining.rfind("\n\n", 0, max_length)  # Paragraph
            if split_index == -1:
                split_index = remaining.rfind("\n", 0, max_length)  # Line
            if split_index == -1:
                split_index = max_length  # Hard split

            parts.append(remaining[:split_index])
            remaining = remaining[split_index:].strip()

        return parts

    @retry_with_backoff(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(requests.exceptions.RequestException, ConnectionError)
    )
    def _send_single_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML",
        disable_preview: bool = True
    ) -> bool:
        """
        Send a single message (internal).

        Args:
            text: Message text
            chat_id: Target chat ID
            parse_mode: Parse mode (HTML or None)
            disable_preview: Disable link previews

        Returns:
            True if successful
        """
        chat_id = chat_id or self.chat_id

        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
            "disable_notification": False
        }

        if parse_mode:
            payload["parse_mode"] = parse_mode

        url = f"{self.api_url}/sendMessage"
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True

    def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        convert_markdown: bool = True
    ) -> bool:
        """
        Send a message to Telegram.

        Handles long messages by splitting them and falls back to
        plain text if HTML formatting fails.

        Args:
            text: Message text (can include Markdown)
            chat_id: Target chat ID (defaults to configured chat_id)
            convert_markdown: Convert Markdown to HTML

        Returns:
            True if all parts sent successfully
        """
        if not self._validate_credentials():
            return False

        if not text or not text.strip():
            return True

        # Convert Markdown to HTML if requested
        formatted_text = self.markdown_to_html(text) if convert_markdown else text

        # Split into parts if needed
        parts = self.split_message(formatted_text)

        success = True
        for i, part in enumerate(parts):
            content = part
            if len(parts) > 1:
                content = f"[{i + 1}/{len(parts)}]\n{part}"

            try:
                # Attempt 1: HTML
                self._send_single_message(content, chat_id, parse_mode="HTML")
                logger.info(
                    f"Sent message part",
                    extra={"part": i + 1, "total": len(parts)}
                )

            except requests.exceptions.HTTPError as e:
                # Attempt 2: Plain text fallback
                if hasattr(e, 'response') and e.response.status_code == 400:
                    logger.warning(
                        f"HTML error, retrying as plain text",
                        extra={"part": i + 1}
                    )
                    try:
                        self._send_single_message(
                            content,
                            chat_id,
                            parse_mode=None
                        )
                    except Exception as fallback_error:
                        logger.error(
                            f"Failed to send message part",
                            extra={"part": i + 1, "error": str(fallback_error)}
                        )
                        success = False
                else:
                    logger.error(
                        f"Failed to send message part",
                        extra={"part": i + 1, "error": str(e)}
                    )
                    success = False

            except Exception as e:
                logger.error(
                    f"Failed to send message part",
                    extra={"part": i + 1, "error": str(e)}
                )
                success = False

            # Small delay between parts to avoid rate limiting
            if i < len(parts) - 1:
                time.sleep(0.5)

        return success


# Singleton client for convenience
_client: Optional[TelegramClient] = None


def get_client() -> TelegramClient:
    """Get or create a singleton TelegramClient instance."""
    global _client
    if _client is None:
        _client = TelegramClient()
    return _client


def send_telegram_message(
    text: str,
    parse_mode: str = "HTML"
) -> bool:
    """
    Send a message to Telegram (backwards compatible function).

    Args:
        text: Message text
        parse_mode: Parse mode (ignored, always uses HTML with fallback)

    Returns:
        True if successful
    """
    return get_client().send_message(text)
