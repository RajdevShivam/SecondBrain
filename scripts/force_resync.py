#!/usr/bin/env python3
"""
Force Re-sync - Resets Notion page statuses to trigger re-ingestion.

This script sets all pages in IDEAS and KNOWLEDGE_BASE to "Update Graph"
status so master_sync.py will re-process them.

Usage:
    python scripts/force_resync.py
    python scripts/force_resync.py --dry-run  # Preview without changes
"""

import sys
import os
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.notion_client import NotionClient

logger = get_logger(__name__)


def fetch_all_pages(notion_client, db_id, db_name):
    """Fetch all pages from a database (handles pagination)."""
    all_pages = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        results = notion_client.query_database(db_id, payload, raw_response=True)

        if not results:
            break

        pages = results.get("results", [])
        all_pages.extend(pages)

        has_more = results.get("has_more", False)
        start_cursor = results.get("next_cursor")

        logger.info(f"Fetched {len(all_pages)} pages from {db_name}...")

    return all_pages


def reset_page_status(notion_client, page_id, dry_run=False):
    """Set a page's Processing Status to 'Update Graph'."""
    if dry_run:
        return True

    try:
        notion_client.update_status(page_id, "Update Graph")
        return True
    except Exception as e:
        logger.error(f"Failed to update {page_id}: {e}")
        return False


def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("FORCE RE-SYNC - Reset Notion Statuses")
    logger.info("=" * 50)

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    # Validate config
    try:
        settings.validate_notion()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    notion_client = NotionClient()

    # Databases to reset
    databases = [
        ("IDEAS", settings.ideas_db_id),
        ("KNOWLEDGE_BASE", settings.knowledge_db_id),
    ]

    total_updated = 0

    for db_name, db_id in databases:
        if not db_id:
            logger.warning(f"Skipping {db_name} - no database ID configured")
            continue

        logger.info(f"\nProcessing {db_name}...")

        # Fetch all pages
        pages = fetch_all_pages(notion_client, db_id, db_name)
        logger.info(f"Found {len(pages)} pages in {db_name}")

        # Update each page
        updated = 0
        for page in pages:
            page_id = page["id"]

            # Get current status
            props = page.get("properties", {})
            status_prop = props.get("Processing Status", {})
            current_status = status_prop.get("select", {})
            current_status_name = current_status.get("name", "Unknown") if current_status else "None"

            # Skip if already set to update
            if current_status_name == "Update Graph":
                continue

            if reset_page_status(notion_client, page_id, dry_run):
                updated += 1
                if updated % 10 == 0:
                    logger.info(f"Updated {updated} pages...")

            # Rate limiting
            if not dry_run:
                time.sleep(0.35)  # Notion rate limit: ~3 requests/sec

        logger.info(f"Updated {updated} pages in {db_name}")
        total_updated += updated

    logger.info("=" * 50)
    logger.info(f"COMPLETE: {total_updated} pages marked for re-sync")

    if dry_run:
        logger.info("\nTo apply changes, run without --dry-run flag")
    else:
        logger.info("\nNow run: python master_sync.py")


if __name__ == "__main__":
    main()
