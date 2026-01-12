"""
Pytest Configuration and Fixtures for SecondBrain Tests

Provides common fixtures for testing the SecondBrain package.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for all tests."""
    monkeypatch.setenv("NOTION_TOKEN", "test-notion-token")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("CAPTURE_INBOX_DB_ID", "abc123def456abc123def456abc123de")
    monkeypatch.setenv("IDEAS_DB_ID", "abc123def456abc123def456abc123de")
    monkeypatch.setenv("KNOWLEDGE_BASE_DB_ID", "abc123def456abc123def456abc123de")
    monkeypatch.setenv("PEOPLE_DB_ID", "abc123def456abc123def456abc123de")
    monkeypatch.setenv("ADMIN_DB_ID", "abc123def456abc123def456abc123de")


@pytest.fixture
def mock_notion_response():
    """Create a mock Notion API response."""
    return {
        "results": [
            {
                "id": "page-123",
                "properties": {
                    "Title": {
                        "title": [{"plain_text": "Test Page"}]
                    },
                    "Processing Status": {
                        "select": {"name": "New"}
                    }
                },
                "url": "https://notion.so/page-123"
            }
        ]
    }


@pytest.fixture
def mock_page():
    """Create a mock page dict."""
    return {
        "page_id": "page-123",
        "text": "This is a test page about Bitcoin and Ethereum.",
        "title": "Test Page",
        "current_status": "New",
        "notion_url": "https://notion.so/page-123"
    }


@pytest.fixture
def mock_extraction():
    """Create a mock extraction result."""
    return {
        "concepts": [
            {"name": "Bitcoin", "type": "Cryptocurrency", "aliases": ["BTC"]},
            {"name": "Ethereum", "type": "Cryptocurrency", "aliases": ["ETH"]}
        ],
        "triplets": [
            {
                "subject": "Bitcoin",
                "relation": "CORRELATES_WITH",
                "object": "Ethereum",
                "confidence": 0.85,
                "context": "Both are major cryptocurrencies"
            }
        ],
        "domain": "Finance"
    }


@pytest.fixture
def mock_neo4j_session():
    """Create a mock Neo4j session."""
    session = MagicMock()
    session.run.return_value = MagicMock()
    return session


@pytest.fixture
def mock_neo4j_driver(mock_neo4j_session):
    """Create a mock Neo4j driver."""
    driver = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=mock_neo4j_session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


@pytest.fixture
def mock_requests_post():
    """Create a mock for requests.post."""
    with patch("requests.post") as mock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        response.raise_for_status = MagicMock()
        mock.return_value = response
        yield mock


@pytest.fixture
def mock_requests_get():
    """Create a mock for requests.get."""
    with patch("requests.get") as mock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"results": []}
        response.raise_for_status = MagicMock()
        mock.return_value = response
        yield mock
