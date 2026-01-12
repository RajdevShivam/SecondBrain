# SecondBrain Project

A professional-grade personal knowledge management system that automates the capture, classification, and synthesis of information using AI.

## Architecture Overview

```
Telegram ──► Pipedream (real-time) ──► Notion DBs (5 databases)
                                              │
GitHub Actions (scheduled) ───────────────────┤
  - master_sync.py (every 30 min)             │
  - daily_nudge.py (8 AM IST)                 ├──► Neo4j Knowledge Graph
  - weekly_review.py (Sun 7 PM IST)           │
                                              │
                                              ▼
                                        Telegram Notifications
```

## Core Components

### 1. Notion Databases
- **Capture Inbox** - Raw captures from Telegram/quick notes
- **Ideas** - Developed ideas and concepts
- **Knowledge Base** - Study notes, articles, resources
- **People** - Contacts and relationship tracking
- **Admin** - Tasks and to-dos

### 2. Neo4j Knowledge Graph
- Stores concepts as nodes with properties (name, type, aliases, domains)
- Relationships between concepts (e.g., Bitcoin CORRELATES_WITH Ethereum)
- Tracks `source_page` for provenance
- Uses `first_linked` and `last_updated` timestamps

### 3. Python Automation Scripts
- **master_sync.py** - Syncs Notion to Neo4j, classifies and routes items
- **daily_nudge.py** - Morning briefing with pending tasks and ideas
- **weekly_review.py** - Comprehensive weekly analysis with graph insights

### 4. Pipedream Workflows (standalone)
Located in `pipeddream/` - these are standalone scripts that run on Pipedream:
- `classify_with_gemini_pd.py` - AI classification of incoming messages
- `extract_triplets_pd.py` - Knowledge graph triplet extraction
- `route_to_database_pd.py` - Routes items to appropriate Notion DBs
- `graph_query_handler_pd.py` - Natural language graph queries
- `fix_handler_pd.py` - Correction handling for misclassifications

**Note:** Pipedream scripts cannot import from other files - they must be self-contained.

## Project Structure

```
SecondBrain/
├── secondbrain/              # Core Python package
│   ├── __init__.py
│   ├── config.py             # Centralized settings (pydantic)
│   ├── logger.py             # Structured JSON logging
│   ├── retry.py              # Retry decorator with backoff
│   ├── dictionaries.py       # Synonyms, abbreviations, context hints
│   ├── notion_client.py      # Notion API wrapper
│   ├── neo4j_client.py       # Neo4j connection manager
│   ├── telegram_client.py    # Telegram messaging
│   ├── sync_state.py         # Database consistency tracking
│   └── graph_services.py     # Deduplication, health, clustering
├── tests/                    # Test suite
│   ├── conftest.py           # Shared fixtures
│   ├── test_config.py
│   ├── test_dictionaries.py
│   └── test_sync_state.py
├── pipeddream/               # Pipedream standalone scripts
├── master_sync.py            # Main sync script
├── daily_nudge.py            # Daily briefing script
├── weekly_review.py          # Weekly review script
├── telegream_utils.py        # Legacy Telegram utils (kept for compatibility)
├── nudges.yml                # GitHub Actions workflow
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
└── .gitignore                # Git exclusions (includes .env)
```

## Key Features

### Normalization System (3-tier)
1. **Unambiguous** - Direct mappings (BTC → Bitcoin, GARCH → full name)
2. **Ambiguous** - Context-dependent (PE → Price To Earnings OR Private Equity)
3. **Context Hints** - Keywords to disambiguate ambiguous terms

### Database Consistency
- Content hashing to detect changes
- SyncState nodes in Neo4j track what's been synced
- Status-based transaction flow: New → Processing → Routing → Processed

### Graph Services
- Concept deduplication with fuzzy matching
- Graph health metrics (orphans, stale concepts)
- Knowledge decay tracking (flag unused concepts)

## Development Guidelines

### Environment Setup
1. Copy `.env.example` to `.env` and fill in your values
2. Install dependencies: `pip install -r requirements.txt`
3. Run tests: `pytest tests/`

### Testing
- All tests go in `tests/` folder
- Use pytest fixtures from `conftest.py`
- Mock external APIs (Notion, Neo4j, Telegram)

### Code Style
- Use structured logging from `secondbrain.logger`
- Use `secondbrain.config.settings` for configuration
- Add retry logic for external API calls
- No hardcoded secrets - everything from environment variables

### Secrets Management
- All secrets stored in `.env` (gitignored)
- GitHub Actions uses repository secrets
- Never commit API keys, tokens, or database IDs

## API Integrations

### Notion API
- Version: 2022-06-28
- Rate limits: Handled with MAX_WORKERS=2 for parallel requests

### Google Gemini
- Model: gemini-2.0-flash-lite-preview-02-05
- Used for classification, triplet extraction, and summaries

### Neo4j AuraDB
- Graph database for knowledge storage
- Uses Cypher queries with parameterized inputs

### Telegram Bot API
- HTML parse mode with Markdown fallback
- Message splitting for long texts (4000 char limit)

## Scheduled Jobs (GitHub Actions)

| Schedule | Script | Purpose |
|----------|--------|---------|
| Every 30 min | master_sync.py | Sync Notion to Neo4j |
| 8:00 AM IST | daily_nudge.py | Morning briefing |
| Sun 7:00 PM IST | weekly_review.py | Weekly analysis |

## Common Tasks

### Run sync manually
```bash
python master_sync.py
```

### Run tests
```bash
pytest tests/ -v
```

### Check for duplicates in graph
```python
from secondbrain.graph_services import get_deduplicator
dedup = get_deduplicator()
duplicates = dedup.find_duplicates()
```

### Get graph health metrics
```python
from secondbrain.graph_services import get_health_checker
health = get_health_checker()
report = health.get_health_report()
```
