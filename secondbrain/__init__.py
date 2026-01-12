"""
SecondBrain Core Package

A professional knowledge management system that syncs Notion databases
to a Neo4j knowledge graph with AI-powered classification and analysis.

Modules:
    config          - Centralized configuration management
    logger          - Structured JSON logging
    retry           - Retry decorator with exponential backoff
    dictionaries    - Domain-specific synonyms and abbreviations
    notion_client   - Notion API wrapper with retry logic
    neo4j_client    - Neo4j connection manager
    telegram_client - Telegram messaging utilities
    sync_state      - Database consistency tracking
    graph_services  - Graph health, deduplication, clustering
"""

from secondbrain.config import settings
from secondbrain.logger import get_logger

__version__ = "1.0.0"
__all__ = [
    "settings",
    "get_logger",
]
