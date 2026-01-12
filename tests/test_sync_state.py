"""
Tests for secondbrain.sync_state module
"""

import pytest
from unittest.mock import MagicMock, patch

from secondbrain.sync_state import SyncStateManager


class TestComputeContentHash:
    """Tests for content hash computation."""

    def test_hash_stability(self):
        """Same content should always produce same hash."""
        page = {"title": "Test", "text": "Hello World"}

        hash1 = SyncStateManager.compute_content_hash(page)
        hash2 = SyncStateManager.compute_content_hash(page)

        assert hash1 == hash2

    def test_hash_ignores_case(self):
        """Hash should normalize case."""
        page1 = {"title": "TEST", "text": "HELLO WORLD"}
        page2 = {"title": "test", "text": "hello world"}

        hash1 = SyncStateManager.compute_content_hash(page1)
        hash2 = SyncStateManager.compute_content_hash(page2)

        assert hash1 == hash2

    def test_hash_ignores_whitespace(self):
        """Hash should normalize whitespace."""
        page1 = {"title": "Test", "text": "Hello   World"}
        page2 = {"title": "Test", "text": "Hello World"}

        hash1 = SyncStateManager.compute_content_hash(page1)
        hash2 = SyncStateManager.compute_content_hash(page2)

        assert hash1 == hash2

    def test_hash_detects_content_change(self):
        """Different content should produce different hash."""
        page1 = {"title": "Test", "text": "Hello"}
        page2 = {"title": "Test", "text": "Hello World"}

        hash1 = SyncStateManager.compute_content_hash(page1)
        hash2 = SyncStateManager.compute_content_hash(page2)

        assert hash1 != hash2

    def test_hash_detects_title_change(self):
        """Different title should produce different hash."""
        page1 = {"title": "Test A", "text": "Hello"}
        page2 = {"title": "Test B", "text": "Hello"}

        hash1 = SyncStateManager.compute_content_hash(page1)
        hash2 = SyncStateManager.compute_content_hash(page2)

        assert hash1 != hash2

    def test_hash_is_sha256(self):
        """Hash should be a valid SHA-256 hex string."""
        page = {"title": "Test", "text": "Hello"}
        hash_value = SyncStateManager.compute_content_hash(page)

        assert len(hash_value) == 64  # SHA-256 produces 64 hex chars
        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_hash_handles_missing_fields(self):
        """Should handle pages with missing fields."""
        page1 = {"title": "Test"}  # No text
        page2 = {"text": "Hello"}  # No title

        # Should not raise
        hash1 = SyncStateManager.compute_content_hash(page1)
        hash2 = SyncStateManager.compute_content_hash(page2)

        assert hash1 is not None
        assert hash2 is not None


class TestNeedsSync:
    """Tests for the needs_sync method."""

    @pytest.fixture
    def mock_manager(self):
        """Create a SyncStateManager with mocked Neo4j."""
        with patch("secondbrain.sync_state.get_neo4j_client") as mock_get:
            mock_neo4j = MagicMock()
            mock_get.return_value = mock_neo4j
            manager = SyncStateManager(mock_neo4j)
            yield manager, mock_neo4j

    def test_new_page_needs_sync(self, mock_manager):
        """New pages (no sync state) should need sync."""
        manager, mock_neo4j = mock_manager
        mock_neo4j.run_query.return_value = []  # No existing state

        page = {"page_id": "new-page", "text": "test", "title": "Test"}
        needs, reason = manager.needs_sync(page)

        assert needs is True
        assert reason == "new"

    def test_reprocess_requested(self, mock_manager):
        """Pages with 'Update Graph' status should need sync."""
        manager, mock_neo4j = mock_manager

        page = {
            "page_id": "page-123",
            "text": "test",
            "title": "Test",
            "current_status": "Update Graph"
        }
        needs, reason = manager.needs_sync(page)

        assert needs is True
        assert reason == "reprocess_requested"

    def test_content_changed(self, mock_manager):
        """Pages with changed content should need sync."""
        manager, mock_neo4j = mock_manager

        # Return existing state with different hash
        mock_neo4j.run_query.return_value = [{
            "page_id": "page-123",
            "content_hash": "old-hash-value",
            "status": "synced"
        }]

        page = {"page_id": "page-123", "text": "new content", "title": "Test"}
        needs, reason = manager.needs_sync(page)

        assert needs is True
        assert reason == "content_changed"

    def test_unchanged_no_sync(self, mock_manager):
        """Unchanged pages should not need sync."""
        manager, mock_neo4j = mock_manager

        page = {"page_id": "page-123", "text": "test", "title": "Test"}
        current_hash = SyncStateManager.compute_content_hash(page)

        mock_neo4j.run_query.return_value = [{
            "page_id": "page-123",
            "content_hash": current_hash,
            "status": "synced"
        }]

        needs, reason = manager.needs_sync(page)

        assert needs is False
        assert reason == "none"

    def test_no_page_id(self, mock_manager):
        """Pages without page_id should not need sync."""
        manager, _ = mock_manager

        page = {"text": "test", "title": "Test"}  # No page_id
        needs, reason = manager.needs_sync(page)

        assert needs is False
        assert reason == "no_page_id"


class TestMarkSynced:
    """Tests for the mark_synced method."""

    @pytest.fixture
    def mock_manager(self):
        """Create a SyncStateManager with mocked Neo4j."""
        with patch("secondbrain.sync_state.get_neo4j_client") as mock_get:
            mock_neo4j = MagicMock()
            mock_get.return_value = mock_neo4j
            manager = SyncStateManager(mock_neo4j)
            yield manager, mock_neo4j

    def test_mark_synced_success(self, mock_manager):
        """Should successfully mark a page as synced."""
        manager, mock_neo4j = mock_manager
        mock_neo4j.run_query.return_value = []

        result = manager.mark_synced(
            page_id="page-123",
            content_hash="abc123",
            concepts=["Bitcoin", "Ethereum"],
            relations_count=5,
            source_db="IDEAS"
        )

        assert result is True
        mock_neo4j.run_query.assert_called_once()

    def test_mark_synced_with_routing(self, mock_manager):
        """Should include routing info when provided."""
        manager, mock_neo4j = mock_manager
        mock_neo4j.run_query.return_value = []

        result = manager.mark_synced(
            page_id="inbox-123",
            content_hash="abc123",
            concepts=["Bitcoin"],
            relations_count=2,
            source_db="INBOX",
            routed_to_page_id="ideas-456",
            routed_to_db="IDEAS"
        )

        assert result is True
        call_args = mock_neo4j.run_query.call_args
        params = call_args[0][1]
        assert params["routed_to_page_id"] == "ideas-456"
        assert params["routed_to_db"] == "IDEAS"
