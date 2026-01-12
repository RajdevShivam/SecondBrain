# SecondBrain Project

A professional-grade personal knowledge management system that automates the capture, classification, and synthesis of information using AI.

## Architecture Overview

```
Telegram Bot ──► Pipedream (real-time) ──► Notion DBs (5 databases)
                                                  │
GitHub Actions (scheduled) ───────────────────────┤
  - master_sync.py (:15, :45 every hour)          │
  - daily_nudge.py (8:00 AM IST)                  ├──► Neo4j Knowledge Graph
  - weekly_review.py (Sun 7:00 PM IST)            │
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
├── scripts/                  # One-time utility scripts
│   ├── reset_neo4j.py        # Wipe Neo4j database
│   ├── force_resync.py       # Mark Notion pages for re-sync
│   ├── neo4j_diagnostics.py  # Graph health diagnostics
│   └── test_telegram.py      # Test Telegram integration
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

### Exponential Backoff Retry Logic
All API calls include automatic retry with exponential backoff:
- **Gemini API**: 3 retries, 2s base delay (2s → 4s → 8s)
  - Handles 429 rate limits, timeouts, network errors
  - Weekly review uses 120s timeout for large prompts
- **Notion API**: 3 retries, 1s base delay via `@retry_with_backoff` decorator
- **Telegram API**: 2 retries, 1s base delay + HTML → plain text fallback

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
- Retry logic: 3 retries with 1s base delay

### Google Gemini
- Model: gemini-2.0-flash-lite-preview-02-05
- Used for classification, triplet extraction, and summaries
- Retry logic: 3 retries with 2s base delay (exponential backoff)
- Default timeout: 30s (120s for weekly reviews)

### Neo4j AuraDB
- Graph database for knowledge storage
- Uses Cypher queries with parameterized inputs

### Telegram Bot API
- HTML parse mode with Markdown fallback
- Message splitting for long texts (4000 char limit)
- Retry logic: 2 retries with 1s base delay

## Scheduled Jobs (GitHub Actions)

| Schedule | Script | Purpose |
|----------|--------|---------|
| :15 and :45 every hour | master_sync.py | Sync Notion to Neo4j |
| 8:00 AM IST (02:30 UTC) | daily_nudge.py | Morning briefing |
| Sun 7:00 PM IST (13:30 UTC) | weekly_review.py | Weekly analysis |

**Note:** Master sync runs at :15 and :45 to avoid conflicts with other scheduled jobs.

## Common Tasks

### Run sync manually
```bash
python master_sync.py
```

### Run tests
```bash
pytest tests/ -v
```

### Reset Neo4j and re-sync
```bash
# 1. Reset Neo4j database (DESTRUCTIVE!)
python scripts/reset_neo4j.py --confirm

# 2. Mark all Notion pages for re-sync
python scripts/force_resync.py

# 3. Run master sync
python master_sync.py
```

### Check graph health
```bash
python scripts/neo4j_diagnostics.py
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

## Troubleshooting

### Gemini 429 Rate Limit Errors
The system automatically retries with exponential backoff. If issues persist:
- Increase `GEMINI_TIMEOUT` in `.env` to 60 or 120 seconds
- Check logs for retry attempts: `Rate limited (429), retrying in X.Xs`

### Items marked "Reviewing" status
These are items where AI classification failed. To fix:
1. Open Capture Inbox in Notion
2. Filter by `Processing Status = "Reviewing"`
3. For each item: read content, set `Category`, change status to `New`
4. Next sync will pick them up automatically

### Weekly review timeout
If weekly review times out:
- The code now uses 120s timeout (increased from 30s)
- Retry logic will attempt 3 times with backoff
- If still fails, fallback review with stats is sent
