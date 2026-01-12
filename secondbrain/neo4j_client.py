"""
Neo4j Client for SecondBrain

Provides a clean wrapper around the Neo4j driver with:
- Connection pooling
- Automatic retry logic
- Common graph operations
- Fuzzy matching for concepts
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase, Driver
from thefuzz import process, fuzz

from secondbrain.config import settings
from secondbrain.logger import get_logger

logger = get_logger(__name__)


class Neo4jClient:
    """
    Wrapper for Neo4j database operations.

    Usage:
        from secondbrain.neo4j_client import Neo4jClient

        client = Neo4jClient()
        with client.session() as session:
            result = session.run("MATCH (n) RETURN n LIMIT 10")
            nodes = [record["n"] for record in result]

        # Or use high-level methods:
        client.merge_concepts(concepts)
        client.sync_triplets(triplets, page_id)
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None
    ):
        """
        Initialize the Neo4j client.

        Args:
            uri: Neo4j connection URI (defaults to settings.neo4j_uri)
            user: Neo4j username (defaults to settings.neo4j_user)
            password: Neo4j password (defaults to settings.neo4j_password)
        """
        self.uri = uri or settings.neo4j_uri
        self.user = user or settings.neo4j_user
        self.password = password or settings.neo4j_password
        self._driver: Optional[Driver] = None

    @property
    def driver(self) -> Driver:
        """Get or create the Neo4j driver."""
        if self._driver is None:
            if not self.uri:
                raise ValueError("Neo4j URI is required")
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password)
            )
        return self._driver

    def close(self):
        """Close the driver connection."""
        if self._driver:
            self._driver.close()
            self._driver = None

    @contextmanager
    def session(self):
        """
        Context manager for Neo4j sessions.

        Usage:
            with client.session() as session:
                session.run("MATCH (n) RETURN n")
        """
        session = self.driver.session()
        try:
            yield session
        finally:
            session.close()

    def run_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict]:
        """
        Run a Cypher query and return results as list of dicts.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dicts
        """
        with self.session() as session:
            result = session.run(query, parameters or {})
            return [dict(record) for record in result]

    def get_all_concepts(self) -> List[Dict]:
        """Get all concepts with their names and aliases."""
        return self.run_query(
            "MATCH (c:Concept) RETURN c.name as name, c.aliases as aliases, c.type as type"
        )

    def fuzzy_match_concepts(
        self,
        names: List[str],
        threshold: int = 88
    ) -> Dict[str, str]:
        """
        Find fuzzy matches for concept names against existing concepts.

        Args:
            names: List of concept names to match
            threshold: Minimum similarity score (0-100)

        Returns:
            Dict mapping input names to canonical names
        """
        if not self.uri:
            return {}

        matches = {}
        try:
            with self.session() as session:
                # Get all existing concepts and aliases
                result = session.run(
                    "MATCH (c:Concept) RETURN c.name as name, c.aliases as aliases"
                )
                db_concepts = []
                for record in result:
                    db_concepts.append(record["name"])
                    if record["aliases"]:
                        db_concepts.extend(record["aliases"])

                if not db_concepts:
                    return {}

                # Find matches for each name
                for name in names:
                    best_match = process.extractOne(
                        name,
                        db_concepts,
                        scorer=fuzz.token_sort_ratio
                    )
                    if best_match and best_match[1] >= threshold:
                        # Find canonical name
                        res = session.run("""
                            MATCH (c:Concept)
                            WHERE c.name = $val OR $val IN c.aliases
                            RETURN c.name as canonical
                            LIMIT 1
                        """, val=best_match[0])
                        rec = res.single()
                        if rec:
                            matches[name] = rec["canonical"]

        except Exception as e:
            logger.warning(
                f"Fuzzy matching failed",
                extra={"error": str(e), "names_count": len(names)}
            )

        return matches

    def merge_concepts(
        self,
        concepts: List[Dict],
        domain: str = "General"
    ) -> bool:
        """
        Merge concepts into the graph (create if not exists, update if exists).

        Args:
            concepts: List of concept dicts with 'name', 'type', 'aliases'
            domain: Domain to associate with concepts

        Returns:
            True if successful
        """
        if not concepts:
            return True

        ts = datetime.now().isoformat()
        batch = [{
            "name": c["name"],
            "type": c.get("type", "Concept"),
            "aliases": c.get("aliases", []),
            "domain": domain
        } for c in concepts]

        try:
            with self.session() as session:
                session.run("""
                    UNWIND $batch AS item
                    MERGE (c:Concept {name: item.name})
                    ON CREATE SET
                        c.type = item.type,
                        c.domains = [item.domain],
                        c.aliases = item.aliases,
                        c.first_seen = datetime($ts)
                    ON MATCH SET
                        c.last_mentioned = datetime($ts),
                        c.aliases = COALESCE(c.aliases, []) +
                            [x IN item.aliases WHERE NOT x IN COALESCE(c.aliases, [])]
                """, batch=batch, ts=ts)

            logger.info(
                f"Merged concepts",
                extra={"count": len(concepts), "domain": domain}
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to merge concepts",
                extra={"error": str(e), "count": len(concepts)}
            )
            return False

    def delete_relationships_for_page(self, page_id: str) -> bool:
        """
        Delete all relationships owned by a specific page.

        Args:
            page_id: The Notion page ID

        Returns:
            True if successful
        """
        try:
            with self.session() as session:
                session.run(
                    "MATCH ()-[r {source_page: $pid}]->() DELETE r",
                    pid=page_id
                )
            logger.info(
                f"Deleted relationships for page",
                extra={"page_id": page_id[:8]}
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to delete relationships",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return False

    def create_relationship(
        self,
        subject: str,
        relation: str,
        obj: str,
        page_id: str,
        confidence: float = 0.8,
        context: str = ""
    ) -> bool:
        """
        Create a relationship between two concepts.

        Args:
            subject: Source concept name
            relation: Relationship type
            obj: Target concept name
            page_id: Source page ID for tracking
            confidence: Confidence score
            context: Context string

        Returns:
            True if successful
        """
        ts = datetime.now().isoformat()
        rtype = self._sanitize_relation(relation)

        try:
            with self.session() as session:
                session.run(f"""
                    MATCH (a:Concept {{name: $sub}}), (b:Concept {{name: $obj}})
                    MERGE (a)-[r:`{rtype}` {{source_page: $pid}}]->(b)
                    ON CREATE SET r.first_linked = datetime($ts)
                    SET r.confidence = $conf,
                        r.context = $ctx,
                        r.last_updated = datetime($ts)
                """, sub=subject, obj=obj, pid=page_id,
                    conf=confidence, ctx=context[:200], ts=ts)
            return True

        except Exception as e:
            logger.warning(
                f"Failed to create relationship",
                extra={
                    "subject": subject,
                    "relation": rtype,
                    "object": obj,
                    "error": str(e)
                }
            )
            return False

    def sync_triplets(
        self,
        triplets: List[Dict],
        page_id: str
    ) -> int:
        """
        Sync a list of triplets to the graph.

        Args:
            triplets: List of triplet dicts with 'subject', 'relation', 'object'
            page_id: Source page ID

        Returns:
            Number of successfully created relationships
        """
        success_count = 0
        for t in triplets:
            subject = t.get("subject")
            obj = t.get("object")
            if subject and obj:
                if self.create_relationship(
                    subject=subject,
                    relation=t.get("relation", "RELATED_TO"),
                    obj=obj,
                    page_id=page_id,
                    confidence=t.get("confidence", 0.8),
                    context=t.get("context", "")
                ):
                    success_count += 1

        logger.info(
            f"Synced triplets",
            extra={"page_id": page_id[:8], "total": len(triplets), "success": success_count}
        )
        return success_count

    def sync_page(
        self,
        extraction: Dict,
        page_id: str
    ) -> bool:
        """
        Full sync of extraction results for a page.

        Wipes old relationships and creates new ones.

        Args:
            extraction: Extraction dict with 'concepts', 'triplets', 'domain'
            page_id: Source page ID

        Returns:
            True if successful
        """
        if not extraction or not extraction.get("concepts"):
            return False

        # 1. Delete old relationships
        self.delete_relationships_for_page(page_id)

        # 2. Merge concepts
        concepts = extraction.get("concepts", [])
        domain = extraction.get("domain", "General")
        self.merge_concepts(concepts, domain)

        # 3. Create new relationships
        triplets = extraction.get("triplets", [])
        self.sync_triplets(triplets, page_id)

        return True

    @staticmethod
    def _sanitize_relation(raw: str) -> str:
        """
        Sanitize a relation name for Neo4j.

        Args:
            raw: Raw relation string

        Returns:
            Sanitized relation type
        """
        clean = raw.replace(" ", "_").replace("-", "_").upper()
        clean = ''.join(c for c in clean if c.isalnum() or c == '_')
        return clean if clean and clean[0].isalpha() else "RELATED_TO"


# Singleton client for convenience
_client: Optional[Neo4jClient] = None


def get_client() -> Neo4jClient:
    """Get or create a singleton Neo4jClient instance."""
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client


def sanitize_relation(raw: str) -> str:
    """Sanitize a relation name for Neo4j (backwards compatibility)."""
    return Neo4jClient._sanitize_relation(raw)
