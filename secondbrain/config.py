"""
Centralized Configuration Management for SecondBrain

All settings are loaded from environment variables with sensible defaults.
Uses pydantic-settings for type validation and .env file loading.
"""

import os
import re
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


def clean_notion_id(dirty_id: Optional[str]) -> Optional[str]:
    """Extracts 32-char UUID from URL or messy string."""
    if not dirty_id:
        return None
    clean = re.sub(r'[\[\]\(\)]', '', dirty_id)
    match = re.search(r'([a-f0-9]{32})', clean)
    return match.group(1) if match else clean.strip()


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All Notion database IDs are automatically cleaned to extract UUIDs.
    """

    # API Keys & Tokens
    notion_token: str = Field(default="", description="Notion API integration token")
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    telegram_bot_token: str = Field(default="", description="Telegram bot token")
    telegram_chat_id: str = Field(default="", description="Telegram chat ID for notifications")

    # Neo4j Configuration
    neo4j_uri: str = Field(default="", description="Neo4j connection URI")
    neo4j_user: str = Field(default="neo4j", description="Neo4j username")
    neo4j_password: str = Field(default="", description="Neo4j password")

    # Notion Database IDs (raw - will be cleaned)
    capture_inbox_db_id: str = Field(default="", alias="CAPTURE_INBOX_DB_ID")
    ideas_db_id: str = Field(default="", alias="IDEAS_DB_ID")
    knowledge_base_db_id: str = Field(default="", alias="KNOWLEDGE_BASE_DB_ID")
    people_db_id: str = Field(default="", alias="PEOPLE_DB_ID")
    admin_db_id: str = Field(default="", alias="ADMIN_DB_ID")

    # AI Model Configuration
    gemini_model: str = Field(
        default="gemini-2.0-flash-lite-preview-02-05",
        description="Gemini model to use for classification and extraction"
    )

    # API Timeouts (in seconds)
    notion_timeout: int = Field(default=30, description="Timeout for Notion API calls")
    gemini_timeout: int = Field(default=30, description="Timeout for Gemini API calls")
    neo4j_timeout: int = Field(default=30, description="Timeout for Neo4j queries")

    # Processing Limits
    max_workers: int = Field(default=2, description="Max parallel workers for ThreadPoolExecutor")
    notion_page_size: int = Field(default=100, description="Page size for Notion queries")
    text_truncation_limit: int = Field(default=3000, description="Max chars to send to AI")

    # Classification Thresholds
    low_confidence_threshold: float = Field(default=0.6, description="Below this, flag for review")
    fuzzy_match_threshold: int = Field(default=88, description="Fuzzy match similarity threshold (0-100)")

    # Retry Configuration
    max_retries: int = Field(default=3, description="Max retry attempts for API calls")
    retry_base_delay: float = Field(default=1.0, description="Base delay for exponential backoff")
    retry_max_delay: float = Field(default=60.0, description="Maximum delay between retries")

    # Notion API Version
    notion_version: str = Field(default="2022-06-28", description="Notion API version")

    # Knowledge Decay Settings
    stale_threshold_days: int = Field(default=90, description="Days before a concept is flagged stale")
    archive_threshold_days: int = Field(default=365, description="Days before a concept can be archived")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def inbox_db_id(self) -> Optional[str]:
        """Clean Capture Inbox database ID."""
        return clean_notion_id(self.capture_inbox_db_id)

    @property
    def ideas_db_id_clean(self) -> Optional[str]:
        """Clean Ideas database ID."""
        return clean_notion_id(self.ideas_db_id)

    @property
    def knowledge_db_id(self) -> Optional[str]:
        """Clean Knowledge Base database ID."""
        return clean_notion_id(self.knowledge_base_db_id)

    @property
    def people_db_id_clean(self) -> Optional[str]:
        """Clean People database ID."""
        return clean_notion_id(self.people_db_id)

    @property
    def admin_db_id_clean(self) -> Optional[str]:
        """Clean Admin database ID."""
        return clean_notion_id(self.admin_db_id)

    @property
    def db_config(self) -> dict:
        """Database configuration dictionary matching existing format."""
        return {
            "INBOX": {"id": self.inbox_db_id, "type": "Inbox"},
            "IDEAS": {"id": self.ideas_db_id_clean, "type": "Idea"},
            "KNOWLEDGE_BASE": {"id": self.knowledge_db_id, "type": "Knowledge"},
            "PEOPLE": {"id": self.people_db_id_clean, "type": "Person"},
            "ADMIN": {"id": self.admin_db_id_clean, "type": "Task"},
        }

    @property
    def notion_headers(self) -> dict:
        """Standard headers for Notion API requests."""
        return {
            "Authorization": f"Bearer {self.notion_token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json"
        }

    def validate_required(self, *keys: str) -> bool:
        """Check if required settings are present."""
        missing = []
        for key in keys:
            value = getattr(self, key, None)
            if not value:
                missing.append(key.upper())
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        return True

    def validate_notion(self) -> bool:
        """Validate Notion-related configuration."""
        return self.validate_required("notion_token")

    def validate_neo4j(self) -> bool:
        """Validate Neo4j-related configuration."""
        return self.validate_required("neo4j_uri", "neo4j_password")

    def validate_telegram(self) -> bool:
        """Validate Telegram-related configuration."""
        return self.validate_required("telegram_bot_token", "telegram_chat_id")

    def validate_gemini(self) -> bool:
        """Validate Gemini-related configuration."""
        return self.validate_required("gemini_api_key")

    def validate_all(self) -> bool:
        """Validate all configuration."""
        self.validate_notion()
        self.validate_neo4j()
        self.validate_telegram()
        self.validate_gemini()
        return True


# Global settings instance - use this throughout the application
settings = Settings()
