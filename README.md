# SecondBrain

A professional-grade personal knowledge management system that automates the capture, classification, and synthesis of information using AI.

## Overview

SecondBrain transforms raw ideas, notes, and information into a structured knowledge graph, providing intelligent insights and daily/weekly summaries via Telegram.

### Key Features

- **Automated Knowledge Capture** - Send messages to Telegram bot for instant capture
- **AI-Powered Classification** - Gemini AI automatically categorizes content into Ideas, Knowledge, Tasks, or People
- **Knowledge Graph** - Neo4j graph database connects concepts and tracks relationships
- **Smart Summaries** - Daily nudges and weekly reviews delivered via Telegram
- **Graph Intelligence** - Discovers patterns, connections, and insights from your knowledge base

## Architecture

```
Telegram Bot ──► Pipedream (real-time) ──► Notion DBs (5 databases)
                                                  │
GitHub Actions (scheduled) ───────────────────────┤
  - master_sync.py (every 30 min)                 │
  - daily_nudge.py (8:00 AM IST)                  ├──► Neo4j Knowledge Graph
  - weekly_review.py (Sun 7:00 PM IST)            │
                                                  │
                                                  ▼
                                        Telegram Notifications
```

### Components

1. **Notion Databases** (5 databases)
   - **Capture Inbox** - Raw captures from Telegram
   - **Ideas** - Developed ideas and concepts
   - **Knowledge Base** - Study notes, articles, resources
   - **People** - Contacts and relationships
   - **Admin** - Tasks and to-dos

2. **Neo4j Knowledge Graph**
   - Stores concepts as nodes with properties
   - Tracks relationships between concepts
   - Maintains provenance and timestamps

3. **Python Automation**
   - `master_sync.py` - Syncs Notion → Neo4j, extracts concepts, builds graph
   - `daily_nudge.py` - Morning briefing with pending tasks
   - `weekly_review.py` - Comprehensive weekly analysis with graph insights

4. **Pipedream Workflows** (standalone)
   - `classify_with_gemini_pd.py` - AI classification
   - `extract_triplets_pd.py` - Knowledge graph triplet extraction
   - `route_to_database_pd.py` - Routes items to appropriate Notion DBs
   - `graph_query_handler_pd.py` - Natural language graph queries
   - `fix_handler_pd.py` - Correction handling

## Project Structure

```
SecondBrain/
├── secondbrain/              # Core Python package
│   ├── config.py             # Centralized settings (pydantic)
│   ├── logger.py             # Structured JSON logging
│   ├── retry.py              # Exponential backoff retry decorator
│   ├── dictionaries.py       # Synonyms, abbreviations, context hints
│   ├── notion_client.py      # Notion API wrapper
│   ├── neo4j_client.py       # Neo4j connection manager
│   ├── telegram_client.py    # Telegram messaging
│   ├── sync_state.py         # Database consistency tracking
│   └── graph_services.py     # Deduplication, health, clustering
├── tests/                    # Test suite (pytest)
├── scripts/                  # One-time utility scripts
│   ├── reset_neo4j.py        # Wipe Neo4j database
│   ├── force_resync.py       # Mark Notion pages for re-sync
│   └── neo4j_diagnostics.py  # Graph health diagnostics
├── pipedream/                # Pipedream standalone scripts
├── master_sync.py            # Main sync script
├── daily_nudge.py            # Daily briefing script
├── weekly_review.py          # Weekly review script
├── nudges.yml                # GitHub Actions workflow
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
└── .gitignore                # Git exclusions
```

## Installation

### Prerequisites

- Python 3.9+
- Notion account with API access
- Neo4j AuraDB instance (free tier available)
- Google Gemini API key
- Telegram bot token
- GitHub account (for scheduled automation)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/SecondBrain.git
   cd SecondBrain
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and database IDs
   ```

4. **Set up Notion databases**
   - Create 5 databases in Notion (Capture Inbox, Ideas, Knowledge Base, People, Admin)
   - Add required properties (see Configuration section)
   - Get database IDs and add to `.env`

5. **Set up Neo4j**
   - Create a free Neo4j AuraDB instance at https://neo4j.com/cloud/aura-free/
   - Get connection URI, username, and password
   - Add credentials to `.env`

6. **Set up Telegram bot**
   - Create a bot via [@BotFather](https://t.me/botfather)
   - Get bot token and your chat ID
   - Add to `.env`

7. **Configure GitHub Actions**
   - Fork the repository
   - Add all environment variables as GitHub Secrets
   - Enable GitHub Actions

## Configuration

### Environment Variables

Required variables in `.env`:

```env
# Notion
NOTION_TOKEN=secret_xxxxx
CAPTURE_INBOX_DB_ID=xxxxx
IDEAS_DB_ID=xxxxx
KNOWLEDGE_BASE_DB_ID=xxxxx
PEOPLE_DB_ID=xxxxx
ADMIN_DB_ID=xxxxx

# Neo4j
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=xxxxx

# Gemini AI
GEMINI_API_KEY=xxxxx

# Telegram
TELEGRAM_BOT_TOKEN=xxxxx
TELEGRAM_CHAT_ID=xxxxx
```

### Notion Database Properties

**Capture Inbox:**
- Title (title)
- Category (select: Idea, Knowledge, Task, People)
- Processing Status (select: New, Processing, Routing, Processed, Reviewing)
- Captured Date (date)

**Ideas, Knowledge Base, People, Admin:**
- Title/Name (title)
- Processing Status (select: New, Update Graph, Active, Processed)
- Status (select) - varies by database

## Usage

### Local Development

Run scripts manually:

```bash
# Sync Notion to Neo4j
python master_sync.py

# Generate daily nudge
python daily_nudge.py

# Generate weekly review
python weekly_review.py
```

### Utility Scripts

```bash
# Reset Neo4j database (destructive!)
python scripts/reset_neo4j.py --confirm

# Mark all Notion pages for re-sync
python scripts/force_resync.py

# Check graph health
python scripts/neo4j_diagnostics.py
```

### Automated Scheduling (GitHub Actions)

Once configured, the system runs automatically:

| Job | Schedule | Description |
|-----|----------|-------------|
| **Master Sync** | Every 30 min (at :15, :45) | Syncs Notion → Neo4j |
| **Daily Nudge** | 8:00 AM IST (02:30 UTC) | Morning briefing |
| **Weekly Review** | Sun 7:00 PM IST (13:30 UTC) | Weekly analysis |

## Features in Detail

### AI Classification

Incoming messages are classified into:
- **Idea** - Creative thoughts, brainstorms, concepts
- **Knowledge** - Facts, learnings, articles, notes
- **Task** - To-dos, action items
- **People** - Contacts, relationships, networking

### Knowledge Graph

Concepts are extracted from text and connected:
- **Normalization** - BTC → Bitcoin, GARCH → Generalized Autoregressive Conditional Heteroskedasticity
- **Disambiguation** - PE → Price-to-Earnings (in finance context) or Private Equity (in VC context)
- **Relationships** - Bitcoin CORRELATES_WITH Ethereum, GARCH USED_FOR Volatility Forecasting

### Retry Logic

All API calls use exponential backoff:
- **Gemini API** - 3 retries, 2s base delay (2s → 4s → 8s)
- **Notion API** - 3 retries, 1s base delay
- **Telegram API** - 2 retries, 1s base delay, HTML → plain text fallback

### Graph Health Metrics

Weekly reviews include:
- Total concepts and relationships
- Orphan concepts (no connections)
- Stale concepts (not mentioned in 90+ days)
- Top 10 most connected concepts

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Style

- Use structured logging from `secondbrain.logger`
- Use `secondbrain.config.settings` for configuration
- Add retry logic for external API calls
- Never commit secrets - use `.env`

### Adding New Features

1. Create feature branch
2. Add code to `secondbrain/` package
3. Add tests to `tests/`
4. Update README
5. Submit pull request

## Troubleshooting

### Gemini 429 Rate Limit Errors

The system uses exponential backoff retry. If you still hit limits:
- Increase `GEMINI_TIMEOUT` in `.env` to 60 or 120 seconds
- Reduce sync frequency in `nudges.yml`

### Neo4j Connection Issues

Check:
- Neo4j instance is running (not paused on free tier)
- Credentials are correct in `.env`
- Firewall allows connection to Neo4j AuraDB

### Notion API Errors

- Verify database IDs are correct (remove hyphens and URL encoding)
- Check Notion integration has access to all databases
- Ensure required properties exist on databases

### GitHub Actions Not Running

- Check GitHub Actions is enabled in repository settings
- Verify all secrets are added to repository
- Check workflow file syntax in `nudges.yml`

## Roadmap

- [ ] Deduplication of similar concepts
- [ ] Concept clustering and community detection
- [ ] Knowledge decay tracking (flag unused concepts)
- [ ] Backup and export functionality
- [ ] User feedback loop for classification corrections
- [ ] Enhanced graph visualizations
- [ ] Mobile app for quick capture

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Built with [Notion API](https://developers.notion.com/)
- Powered by [Google Gemini](https://ai.google.dev/)
- Graph database by [Neo4j](https://neo4j.com/)
- Notifications via [Telegram Bot API](https://core.telegram.org/bots)

---

**Note:** This is a personal knowledge management system. Handle your data with care and never commit API keys or credentials to version control.
