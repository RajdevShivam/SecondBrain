#!/usr/bin/env python3
"""
Reset Neo4j Database - Wipes all nodes and relationships.

WARNING: This is destructive! All data will be deleted.

Usage:
    python scripts/reset_neo4j.py
    python scripts/reset_neo4j.py --confirm  # Skip confirmation prompt
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase
from secondbrain.config import settings
from secondbrain.logger import get_logger

logger = get_logger(__name__)


def get_stats(session):
    """Get current database statistics."""
    stats = {}

    # Count nodes by label
    result = session.run("MATCH (n) RETURN labels(n)[0] as label, count(*) as count")
    stats["nodes"] = {r["label"]: r["count"] for r in result}

    # Count total relationships
    result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
    stats["relationships"] = result.single()["count"]

    return stats


def reset_database(driver):
    """Delete all nodes and relationships."""
    with driver.session() as session:
        # Get stats before deletion
        before_stats = get_stats(session)
        logger.info(f"Before reset: {before_stats}")

        # Delete all relationships first, then nodes
        # Using DETACH DELETE removes nodes and their relationships
        logger.info("Deleting all nodes and relationships...")

        # Delete in batches to avoid memory issues with large graphs
        deleted_total = 0
        batch_size = 1000

        while True:
            result = session.run(f"""
                MATCH (n)
                WITH n LIMIT {batch_size}
                DETACH DELETE n
                RETURN count(*) as deleted
            """)
            deleted = result.single()["deleted"]
            deleted_total += deleted

            if deleted == 0:
                break
            logger.info(f"Deleted {deleted_total} nodes so far...")

        # Verify deletion
        after_stats = get_stats(session)
        logger.info(f"After reset: {after_stats}")

        return deleted_total


def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("NEO4J DATABASE RESET")
    logger.info("=" * 50)

    # Check for --confirm flag
    skip_confirm = "--confirm" in sys.argv

    # Validate config
    try:
        settings.validate_neo4j()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    logger.info(f"Target: {settings.neo4j_uri}")

    # Connect and show current state
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password)
    )

    try:
        with driver.session() as session:
            stats = get_stats(session)

        total_nodes = sum(stats["nodes"].values())
        logger.info(f"Current state: {total_nodes} nodes, {stats['relationships']} relationships")

        if total_nodes == 0:
            logger.info("Database is already empty. Nothing to reset.")
            return

        # Confirmation
        if not skip_confirm:
            print("\n" + "=" * 50)
            print("WARNING: This will DELETE ALL DATA in Neo4j!")
            print(f"  - {total_nodes} nodes")
            print(f"  - {stats['relationships']} relationships")
            print("=" * 50)

            confirm = input("\nType 'DELETE' to confirm: ")
            if confirm != "DELETE":
                logger.info("Aborted. No changes made.")
                return

        # Perform reset
        deleted = reset_database(driver)
        logger.info(f"Reset complete. Deleted {deleted} nodes.")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
