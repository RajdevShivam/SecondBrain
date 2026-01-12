"""
Sync State Management for SecondBrain

Provides database consistency tracking between Notion and Neo4j:
- Content hashing to detect changes
- Sync state tracking in Neo4j
- Orphan detection for incomplete syncs
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.neo4j_client import Neo4jClient, get_client as get_neo4j_client

logger = get_logger(__name__)


class SyncStateManager:
    """
    Manages sync state between Notion and Neo4j.

    Tracks which pages have been synced and detects when content
    has changed and needs re-syncing.

    Usage:
        from secondbrain.sync_state import SyncStateManager

        manager = SyncStateManager()

        # Check if page needs sync
        needs_sync, reason = manager.needs_sync(page)
        if needs_sync:
            # ... process page ...
            manager.mark_synced(page_id, concepts, relations_count)
    """

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None):
        """
        Initialize the sync state manager.

        Args:
            neo4j_client: Neo4j client instance (defaults to singleton)
        """
        self.neo4j = neo4j_client or get_neo4j_client()

    @staticmethod
    def compute_content_hash(page: Dict[str, Any]) -> str:
        """
        Compute a deterministic hash of page content.

        Ignores fields that don't affect the knowledge graph:
        - Processing Status
        - Timestamps
        - Formatting

        Args:
            page: Page dict with 'text', 'title'

        Returns:
            SHA-256 hash of normalized content
        """
        # Normalize content: lowercase, strip whitespace, remove extra spaces
        text = page.get("text", "").lower().strip()
        text = " ".join(text.split())  # Normalize whitespace

        title = page.get("title", "").lower().strip()

        # Create a normalized representation
        normalized = json.dumps({
            "title": title,
            "text": text
        }, sort_keys=True)

        return hashlib.sha256(normalized.encode()).hexdigest()

    def get_sync_state(self, page_id: str) -> Optional[Dict]:
        """
        Get the current sync state for a page.

        Args:
            page_id: Notion page ID

        Returns:
            Sync state dict or None if not found
        """
        try:
            result = self.neo4j.run_query("""
                MATCH (s:SyncState {page_id: $page_id})
                RETURN s.page_id as page_id,
                       s.content_hash as content_hash,
                       s.notion_last_edited as notion_last_edited,
                       s.graph_last_synced as graph_last_synced,
                       s.sync_version as sync_version,
                       s.status as status,
                       s.concepts_extracted as concepts_extracted,
                       s.relations_count as relations_count,
                       s.source_db as source_db,
                       s.routed_to_page_id as routed_to_page_id,
                       s.routed_to_db as routed_to_db
            """, {"page_id": page_id})

            if result:
                return result[0]
            return None

        except Exception as e:
            logger.warning(
                f"Failed to get sync state",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return None

    def needs_sync(self, page: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determine if a page needs to be synced.

        Args:
            page: Page dict with 'page_id', 'text', 'title', 'current_status'

        Returns:
            Tuple of (needs_sync: bool, reason: str)
            Reasons: "new", "content_changed", "reprocess_requested", "none"
        """
        page_id = page.get("page_id", "")
        if not page_id:
            return (False, "no_page_id")

        # Check for explicit reprocess request
        current_status = page.get("current_status", "")
        if current_status == "Update Graph":
            return (True, "reprocess_requested")

        # Get existing sync state
        state = self.get_sync_state(page_id)

        # New page - never synced before
        if not state:
            return (True, "new")

        # Compute current content hash
        current_hash = self.compute_content_hash(page)

        # Content changed
        if current_hash != state.get("content_hash"):
            return (True, "content_changed")

        return (False, "none")

    def mark_synced(
        self,
        page_id: str,
        content_hash: str,
        concepts: List[str],
        relations_count: int,
        source_db: str = "UNKNOWN",
        routed_to_page_id: Optional[str] = None,
        routed_to_db: Optional[str] = None
    ) -> bool:
        """
        Mark a page as successfully synced.

        Args:
            page_id: Notion page ID
            content_hash: Hash of synced content
            concepts: List of concept names extracted
            relations_count: Number of relationships created
            source_db: Source database name
            routed_to_page_id: If routed, the destination page ID
            routed_to_db: If routed, the destination database name

        Returns:
            True if successful
        """
        ts = datetime.now(timezone.utc).isoformat()

        try:
            self.neo4j.run_query("""
                MERGE (s:SyncState {page_id: $page_id})
                ON CREATE SET
                    s.content_hash = $content_hash,
                    s.graph_last_synced = datetime($ts),
                    s.sync_version = 1,
                    s.status = "synced",
                    s.concepts_extracted = $concepts,
                    s.relations_count = $relations_count,
                    s.source_db = $source_db,
                    s.routed_to_page_id = $routed_to_page_id,
                    s.routed_to_db = $routed_to_db
                ON MATCH SET
                    s.content_hash = $content_hash,
                    s.graph_last_synced = datetime($ts),
                    s.sync_version = COALESCE(s.sync_version, 0) + 1,
                    s.status = "synced",
                    s.concepts_extracted = $concepts,
                    s.relations_count = $relations_count,
                    s.routed_to_page_id = COALESCE($routed_to_page_id, s.routed_to_page_id),
                    s.routed_to_db = COALESCE($routed_to_db, s.routed_to_db)
            """, {
                "page_id": page_id,
                "content_hash": content_hash,
                "ts": ts,
                "concepts": concepts,
                "relations_count": relations_count,
                "source_db": source_db,
                "routed_to_page_id": routed_to_page_id,
                "routed_to_db": routed_to_db
            })

            logger.info(
                f"Marked page as synced",
                extra={
                    "page_id": page_id[:8],
                    "concepts_count": len(concepts),
                    "relations_count": relations_count
                }
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to mark page as synced",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return False

    def mark_routing(
        self,
        inbox_page_id: str,
        destination_page_id: str,
        destination_db: str
    ) -> bool:
        """
        Mark an inbox item as routed to a destination.

        Args:
            inbox_page_id: Source inbox page ID
            destination_page_id: Destination page ID
            destination_db: Destination database name

        Returns:
            True if successful
        """
        ts = datetime.now(timezone.utc).isoformat()

        try:
            self.neo4j.run_query("""
                MERGE (s:SyncState {page_id: $page_id})
                SET s.status = "routing",
                    s.routed_to_page_id = $dest_page_id,
                    s.routed_to_db = $dest_db,
                    s.routing_started_at = datetime($ts)
            """, {
                "page_id": inbox_page_id,
                "dest_page_id": destination_page_id,
                "dest_db": destination_db,
                "ts": ts
            })
            return True

        except Exception as e:
            logger.warning(
                f"Failed to mark routing",
                extra={"page_id": inbox_page_id[:8], "error": str(e)}
            )
            return False

    def mark_error(self, page_id: str, error: str) -> bool:
        """
        Mark a page as having a sync error.

        Args:
            page_id: Notion page ID
            error: Error message

        Returns:
            True if successful
        """
        ts = datetime.now(timezone.utc).isoformat()

        try:
            self.neo4j.run_query("""
                MERGE (s:SyncState {page_id: $page_id})
                SET s.status = "error",
                    s.last_error = $error,
                    s.error_at = datetime($ts),
                    s.error_count = COALESCE(s.error_count, 0) + 1
            """, {
                "page_id": page_id,
                "error": error[:500],  # Truncate long errors
                "ts": ts
            })
            return True

        except Exception as e:
            logger.warning(
                f"Failed to mark error",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return False

    def get_orphaned_items(self) -> List[Dict]:
        """
        Find inbox items stuck in "routing" state.

        These are items where the destination was created but
        the process didn't complete.

        Returns:
            List of orphaned sync states
        """
        try:
            return self.neo4j.run_query("""
                MATCH (s:SyncState)
                WHERE s.status = "routing"
                  AND s.routing_started_at < datetime() - duration('PT1H')
                RETURN s.page_id as page_id,
                       s.routed_to_page_id as routed_to_page_id,
                       s.routed_to_db as routed_to_db,
                       s.routing_started_at as routing_started_at
                ORDER BY s.routing_started_at
            """)
        except Exception as e:
            logger.warning(f"Failed to get orphaned items", extra={"error": str(e)})
            return []

    def get_stale_pages(self, days: int = 7) -> List[Dict]:
        """
        Find pages where Notion may have been edited after last sync.

        Note: This requires storing notion_last_edited during sync,
        which would need Notion webhook support for real-time updates.

        Args:
            days: Number of days to consider stale

        Returns:
            List of potentially stale pages
        """
        try:
            return self.neo4j.run_query("""
                MATCH (s:SyncState)
                WHERE s.status = "synced"
                  AND s.graph_last_synced < datetime() - duration({days: $days})
                RETURN s.page_id as page_id,
                       s.graph_last_synced as last_synced,
                       s.concepts_extracted as concepts
                ORDER BY s.graph_last_synced
                LIMIT 50
            """, {"days": days})
        except Exception as e:
            logger.warning(f"Failed to get stale pages", extra={"error": str(e)})
            return []

    def get_sync_stats(self) -> Dict[str, Any]:
        """
        Get overall sync statistics.

        Returns:
            Dict with sync statistics
        """
        try:
            result = self.neo4j.run_query("""
                MATCH (s:SyncState)
                RETURN s.status as status, count(s) as count
            """)

            stats = {"total": 0, "by_status": {}}
            for row in result:
                stats["by_status"][row["status"]] = row["count"]
                stats["total"] += row["count"]

            return stats

        except Exception as e:
            logger.warning(f"Failed to get sync stats", extra={"error": str(e)})
            return {"total": 0, "by_status": {}, "error": str(e)}


# Singleton manager for convenience
_manager: Optional[SyncStateManager] = None


def get_manager() -> SyncStateManager:
    """Get or create a singleton SyncStateManager instance."""
    global _manager
    if _manager is None:
        _manager = SyncStateManager()
    return _manager
