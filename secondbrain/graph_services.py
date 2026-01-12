"""
Graph Services for SecondBrain

Provides advanced graph operations:
- Concept deduplication
- Graph health metrics
- Knowledge decay tracking
- Community detection (clustering)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from thefuzz import fuzz

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.neo4j_client import Neo4jClient, get_client as get_neo4j_client

logger = get_logger(__name__)


class ConceptDeduplicator:
    """
    Identifies and merges duplicate concepts in the knowledge graph.

    Uses fuzzy matching to find similar concepts and provides
    tools to merge them while preserving relationships.
    """

    MERGE_THRESHOLD = 92  # Fuzzy match score threshold

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None):
        """
        Initialize the deduplicator.

        Args:
            neo4j_client: Neo4j client instance
        """
        self.neo4j = neo4j_client or get_neo4j_client()

    def find_duplicates(
        self,
        threshold: Optional[int] = None
    ) -> List[Tuple[str, str, float]]:
        """
        Find candidate duplicate concept pairs.

        Args:
            threshold: Minimum similarity score (0-100)

        Returns:
            List of (concept_a, concept_b, similarity_score) tuples
        """
        threshold = threshold or self.MERGE_THRESHOLD

        try:
            # Get all concepts
            result = self.neo4j.run_query("""
                MATCH (c:Concept)
                RETURN c.name as name, c.aliases as aliases, c.type as type
            """)

            duplicates = []
            concepts = list(result)

            for i, c1 in enumerate(concepts):
                for c2 in concepts[i + 1:]:
                    score = self._compute_similarity(c1, c2)
                    if score >= threshold:
                        duplicates.append((c1["name"], c2["name"], score))

            # Sort by similarity descending
            duplicates.sort(key=lambda x: -x[2])

            logger.info(
                f"Found duplicate candidates",
                extra={"count": len(duplicates), "threshold": threshold}
            )
            return duplicates

        except Exception as e:
            logger.error(f"Failed to find duplicates", extra={"error": str(e)})
            return []

    def _compute_similarity(self, c1: Dict, c2: Dict) -> float:
        """
        Compute similarity between two concepts.

        Considers:
        1. Name fuzzy match
        2. Alias overlap
        3. Type match bonus
        """
        name_score = fuzz.token_sort_ratio(c1["name"], c2["name"])

        # Check if one is alias of other
        aliases1 = set(c1.get("aliases") or [])
        aliases2 = set(c2.get("aliases") or [])

        if c1["name"] in aliases2 or c2["name"] in aliases1:
            return 100  # Definite match

        # Alias overlap bonus
        alias_overlap = 0
        if aliases1 and aliases2:
            overlap = len(aliases1 & aliases2)
            total = len(aliases1 | aliases2)
            alias_overlap = (overlap / total) * 25 if total > 0 else 0

        # Type match bonus
        type_bonus = 5 if c1.get("type") == c2.get("type") else 0

        return name_score * 0.7 + alias_overlap + type_bonus

    def preview_merge(self, keep: str, remove: str) -> Dict[str, Any]:
        """
        Preview what would happen if we merge two concepts.

        Args:
            keep: Concept name to keep
            remove: Concept name to merge into 'keep'

        Returns:
            Preview dict with affected relationships
        """
        try:
            # Get relationships for both concepts
            keep_rels = self.neo4j.run_query("""
                MATCH (c:Concept {name: $name})-[r]-()
                RETURN type(r) as type, count(r) as count
            """, {"name": keep})

            remove_rels = self.neo4j.run_query("""
                MATCH (c:Concept {name: $name})-[r]-()
                RETURN type(r) as type, count(r) as count
            """, {"name": remove})

            return {
                "keep": keep,
                "remove": remove,
                "keep_relationships": list(keep_rels),
                "remove_relationships": list(remove_rels),
                "will_transfer": sum(r["count"] for r in remove_rels)
            }

        except Exception as e:
            logger.error(f"Failed to preview merge", extra={"error": str(e)})
            return {"error": str(e)}

    def merge_concepts(
        self,
        keep: str,
        remove: str,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Merge 'remove' concept into 'keep' concept.

        - Transfers all relationships
        - Merges aliases
        - Archives the removed concept

        Args:
            keep: Concept name to keep
            remove: Concept name to merge into 'keep'
            dry_run: If True, only preview without making changes

        Returns:
            Result dict with merge details
        """
        if dry_run:
            return self.preview_merge(keep, remove)

        try:
            with self.neo4j.session() as session:
                # 1. Transfer incoming relationships
                session.run("""
                    MATCH (other)-[r]->(old:Concept {name: $remove})
                    MATCH (keep:Concept {name: $keep})
                    WHERE NOT (other)-[]->(keep)
                    CREATE (other)-[new_r:RELATED_TO]->(keep)
                    SET new_r = properties(r), new_r.migrated_from = $remove
                """, keep=keep, remove=remove)

                # 2. Transfer outgoing relationships
                session.run("""
                    MATCH (old:Concept {name: $remove})-[r]->(other)
                    MATCH (keep:Concept {name: $keep})
                    WHERE NOT (keep)-[]->(other)
                    CREATE (keep)-[new_r:RELATED_TO]->(other)
                    SET new_r = properties(r), new_r.migrated_from = $remove
                """, keep=keep, remove=remove)

                # 3. Merge aliases
                session.run("""
                    MATCH (old:Concept {name: $remove})
                    MATCH (keep:Concept {name: $keep})
                    SET keep.aliases = COALESCE(keep.aliases, []) + [$remove] +
                        COALESCE(old.aliases, [])
                """, keep=keep, remove=remove)

                # 4. Archive old concept (don't delete for audit trail)
                session.run("""
                    MATCH (old:Concept {name: $remove})
                    SET old:ArchivedConcept,
                        old.merged_into = $keep,
                        old.merged_at = datetime()
                    REMOVE old:Concept
                """, keep=keep, remove=remove)

            logger.info(
                f"Merged concepts",
                extra={"kept": keep, "removed": remove}
            )
            return {"merged": True, "kept": keep, "removed": remove}

        except Exception as e:
            logger.error(f"Failed to merge concepts", extra={"error": str(e)})
            return {"merged": False, "error": str(e)}


class GraphHealthChecker:
    """
    Computes health metrics for the knowledge graph.
    """

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None):
        """
        Initialize the health checker.

        Args:
            neo4j_client: Neo4j client instance
        """
        self.neo4j = neo4j_client or get_neo4j_client()

    def get_health_report(self) -> Dict[str, Any]:
        """
        Get comprehensive health metrics for the graph.

        Returns:
            Dict with health metrics
        """
        stats: Dict[str, Any] = {}

        try:
            # Concept counts
            result = self.neo4j.run_query("""
                MATCH (c:Concept)
                RETURN count(c) as total_concepts,
                       count(DISTINCT c.type) as unique_types
            """)
            if result:
                stats["concepts"] = result[0]

            # Relationship stats
            result = self.neo4j.run_query("""
                MATCH ()-[r]->()
                RETURN count(r) as total_relations,
                       count(DISTINCT type(r)) as unique_relation_types
            """)
            if result:
                stats["relationships"] = result[0]

            # Orphan concepts (no relationships)
            result = self.neo4j.run_query("""
                MATCH (c:Concept)
                WHERE NOT (c)-[]-()
                RETURN count(c) as orphan_count
            """)
            if result:
                stats["orphans"] = result[0]["orphan_count"]

            # Top connected concepts
            result = self.neo4j.run_query("""
                MATCH (c:Concept)-[r]-()
                WITH c, count(r) as connections
                RETURN c.name as name, connections
                ORDER BY connections DESC
                LIMIT 10
            """)
            stats["top_concepts"] = list(result)

            # Average connections per concept
            if stats.get("concepts", {}).get("total_concepts", 0) > 0:
                total_rels = stats.get("relationships", {}).get("total_relations", 0)
                total_concepts = stats["concepts"]["total_concepts"]
                stats["avg_connections"] = round(total_rels / total_concepts, 2)

            logger.info("Generated health report", extra={"stats": stats})
            return stats

        except Exception as e:
            logger.error(f"Failed to get health report", extra={"error": str(e)})
            return {"error": str(e)}


class KnowledgeDecayManager:
    """
    Manages concept freshness and decay.

    Tracks last access/mention times and flags stale concepts.
    """

    def __init__(
        self,
        neo4j_client: Optional[Neo4jClient] = None,
        stale_days: int = 90,
        archive_days: int = 365
    ):
        """
        Initialize the decay manager.

        Args:
            neo4j_client: Neo4j client instance
            stale_days: Days before a concept is flagged stale
            archive_days: Days before a concept can be archived
        """
        self.neo4j = neo4j_client or get_neo4j_client()
        self.stale_days = stale_days
        self.archive_days = archive_days

    def flag_stale_concepts(self) -> int:
        """
        Flag concepts not mentioned in stale_days as :StaleConcept.

        Returns:
            Number of concepts flagged
        """
        try:
            result = self.neo4j.run_query("""
                MATCH (c:Concept)
                WHERE c.last_mentioned < datetime() - duration({days: $days})
                  AND NOT c:StaleConcept
                SET c:StaleConcept, c.flagged_stale_at = datetime()
                RETURN count(c) as flagged
            """, {"days": self.stale_days})

            flagged = result[0]["flagged"] if result else 0
            logger.info(f"Flagged stale concepts", extra={"count": flagged})
            return flagged

        except Exception as e:
            logger.error(f"Failed to flag stale concepts", extra={"error": str(e)})
            return 0

    def get_stale_concepts(self, limit: int = 50) -> List[Dict]:
        """
        Get list of stale concepts for review.

        Args:
            limit: Maximum number to return

        Returns:
            List of stale concept dicts
        """
        try:
            return list(self.neo4j.run_query("""
                MATCH (c:Concept)
                WHERE c:StaleConcept OR
                      c.last_mentioned < datetime() - duration({days: $days})
                OPTIONAL MATCH (c)-[r]-()
                RETURN c.name as name,
                       c.last_mentioned as last_seen,
                       count(r) as connections,
                       c:StaleConcept as is_flagged
                ORDER BY c.last_mentioned
                LIMIT $limit
            """, {"days": self.stale_days, "limit": limit}))

        except Exception as e:
            logger.error(f"Failed to get stale concepts", extra={"error": str(e)})
            return []

    def refresh_concept(self, name: str) -> bool:
        """
        Mark a concept as recently accessed (remove stale flag).

        Args:
            name: Concept name

        Returns:
            True if successful
        """
        try:
            self.neo4j.run_query("""
                MATCH (c:Concept {name: $name})
                REMOVE c:StaleConcept
                SET c.last_mentioned = datetime(),
                    c.refreshed_at = datetime()
            """, {"name": name})

            logger.info(f"Refreshed concept", extra={"name": name})
            return True

        except Exception as e:
            logger.error(f"Failed to refresh concept", extra={"error": str(e)})
            return False


class GraphBackupManager:
    """
    Manages backup and export of graph data.
    """

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None):
        """
        Initialize the backup manager.

        Args:
            neo4j_client: Neo4j client instance
        """
        self.neo4j = neo4j_client or get_neo4j_client()

    def export_concepts(self) -> List[Dict]:
        """
        Export all concepts as a list of dicts.

        Returns:
            List of concept dicts
        """
        try:
            return list(self.neo4j.run_query("""
                MATCH (c:Concept)
                RETURN c.name as name,
                       c.type as type,
                       c.aliases as aliases,
                       c.domains as domains,
                       c.first_seen as first_seen,
                       c.last_mentioned as last_mentioned
            """))
        except Exception as e:
            logger.error(f"Failed to export concepts", extra={"error": str(e)})
            return []

    def export_relationships(self) -> List[Dict]:
        """
        Export all relationships as a list of dicts.

        Returns:
            List of relationship dicts
        """
        try:
            return list(self.neo4j.run_query("""
                MATCH (a:Concept)-[r]->(b:Concept)
                RETURN a.name as source,
                       type(r) as relation,
                       b.name as target,
                       r.confidence as confidence,
                       r.context as context,
                       r.source_page as source_page,
                       r.first_linked as first_linked,
                       r.last_updated as last_updated
            """))
        except Exception as e:
            logger.error(f"Failed to export relationships", extra={"error": str(e)})
            return []

    def export_sync_states(self) -> List[Dict]:
        """
        Export all sync states.

        Returns:
            List of sync state dicts
        """
        try:
            return list(self.neo4j.run_query("""
                MATCH (s:SyncState)
                RETURN s.page_id as page_id,
                       s.content_hash as content_hash,
                       s.status as status,
                       s.concepts_extracted as concepts,
                       s.relations_count as relations_count,
                       s.source_db as source_db,
                       s.graph_last_synced as last_synced
            """))
        except Exception as e:
            logger.error(f"Failed to export sync states", extra={"error": str(e)})
            return []

    def create_full_backup(self) -> Dict[str, Any]:
        """
        Create a complete backup of the graph.

        Returns:
            Backup dict with concepts, relationships, and sync states
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        backup = {
            "timestamp": timestamp,
            "version": "1.0",
            "concepts": self.export_concepts(),
            "relationships": self.export_relationships(),
            "sync_states": self.export_sync_states()
        }

        logger.info(
            f"Created backup",
            extra={
                "concepts": len(backup["concepts"]),
                "relationships": len(backup["relationships"]),
                "sync_states": len(backup["sync_states"])
            }
        )

        return backup


# Convenience functions
def get_deduplicator() -> ConceptDeduplicator:
    """Get a ConceptDeduplicator instance."""
    return ConceptDeduplicator()


def get_health_checker() -> GraphHealthChecker:
    """Get a GraphHealthChecker instance."""
    return GraphHealthChecker()


def get_decay_manager() -> KnowledgeDecayManager:
    """Get a KnowledgeDecayManager instance."""
    return KnowledgeDecayManager()


def get_backup_manager() -> GraphBackupManager:
    """Get a GraphBackupManager instance."""
    return GraphBackupManager()
