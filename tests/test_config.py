"""
Tests for secondbrain.config module
"""

import pytest
from secondbrain.config import Settings, clean_notion_id


class TestCleanNotionId:
    """Tests for the clean_notion_id function."""

    def test_clean_id_from_url(self):
        """Should extract UUID from a Notion URL."""
        url = "https://notion.so/workspace/abc123def456abc123def456abc123de"
        assert clean_notion_id(url) == "abc123def456abc123def456abc123de"

    def test_clean_id_from_raw(self):
        """Should handle already-clean UUIDs."""
        raw = "abc123def456abc123def456abc123de"
        assert clean_notion_id(raw) == "abc123def456abc123def456abc123de"

    def test_clean_id_with_brackets(self):
        """Should remove brackets from ID."""
        bracketed = "[abc123def456abc123def456abc123de]"
        assert clean_notion_id(bracketed) == "abc123def456abc123def456abc123de"

    def test_clean_id_with_parentheses(self):
        """Should remove parentheses from ID."""
        paren = "(abc123def456abc123def456abc123de)"
        assert clean_notion_id(paren) == "abc123def456abc123def456abc123de"

    def test_clean_id_none(self):
        """Should handle None input."""
        assert clean_notion_id(None) is None

    def test_clean_id_empty(self):
        """Should handle empty string."""
        assert clean_notion_id("") is None

    def test_clean_id_with_dashes(self):
        """Should handle UUIDs with dashes in URL."""
        url = "https://notion.so/abc123de-f456-abc1-23de-f456abc123de"
        # The regex looks for 32 consecutive hex chars, so dashes break it
        # This tests the fallback behavior
        result = clean_notion_id(url)
        assert result is not None


class TestSettings:
    """Tests for the Settings class."""

    def test_settings_load_from_env(self):
        """Settings should load from environment variables."""
        settings = Settings()
        assert settings.notion_token == "test-notion-token"
        assert settings.gemini_api_key == "test-gemini-key"
        assert settings.neo4j_uri == "bolt://localhost:7687"

    def test_settings_defaults(self):
        """Settings should have sensible defaults."""
        settings = Settings()
        assert settings.gemini_model == "gemini-2.0-flash-lite-preview-02-05"
        assert settings.max_workers == 2
        assert settings.notion_page_size == 100
        assert settings.low_confidence_threshold == 0.6
        assert settings.fuzzy_match_threshold == 88

    def test_notion_headers(self):
        """Should generate correct Notion headers."""
        settings = Settings()
        headers = settings.notion_headers
        assert "Authorization" in headers
        assert "Bearer test-notion-token" in headers["Authorization"]
        assert headers["Notion-Version"] == "2022-06-28"
        assert headers["Content-Type"] == "application/json"

    def test_db_config(self):
        """Should generate database configuration dict."""
        settings = Settings()
        db_config = settings.db_config
        assert "INBOX" in db_config
        assert "IDEAS" in db_config
        assert "KNOWLEDGE_BASE" in db_config
        assert "PEOPLE" in db_config
        assert "ADMIN" in db_config
        assert db_config["INBOX"]["type"] == "Inbox"

    def test_validate_notion(self):
        """Should validate Notion configuration."""
        settings = Settings()
        assert settings.validate_notion() is True

    def test_validate_notion_missing(self, monkeypatch):
        """Should raise error for missing Notion token."""
        monkeypatch.setenv("NOTION_TOKEN", "")
        settings = Settings()
        with pytest.raises(ValueError, match="NOTION_TOKEN"):
            settings.validate_required("notion_token")

    def test_inbox_db_id_cleaned(self):
        """Should clean database IDs."""
        settings = Settings()
        # The fixture sets a clean ID, so it should match
        assert settings.inbox_db_id == "abc123def456abc123def456abc123de"
