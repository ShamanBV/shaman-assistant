# MagicAnswer Deployment Notes

## Overview
MagicAnswer is a RAG-based Slack bot for internal support. It uses ChromaDB for vector storage and Claude API for answers.

## Services
- **slack_bot.py** - Main Slack bot (Socket Mode)
- **multi_source_rag.py** - RAG system and sync commands

## Environment Variables Required
```
# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_MAGICANSWER_TOKEN=xoxb-...  # Optional, falls back to SLACK_BOT_TOKEN
MAGICANSWER_ADMIN_CHANNEL=C...    # Channel ID for suggestion approvals

# APIs
ANTHROPIC_API_KEY=sk-ant-...
INTERCOM_ACCESS_TOKEN=...

# Database
DB_PATH=./knowledge_base          # ChromaDB storage path
```

## Files to Persist (Stateful)
```
knowledge_base/          # ChromaDB vector database (critical)
*_backup.json           # Collection backups
pending_suggestions.json # User suggestions queue
feedback_log.json       # Feedback data
questions_log.json      # Question analytics
```

## Startup
```bash
# Start bot
python slack_bot.py
```

## Recovery (if DB corrupts)
```bash
rm -rf knowledge_base/
python multi_source_rag.py --restore-all
```

## Backup Commands
```bash
# Export all collections
python multi_source_rag.py --export slack
python multi_source_rag.py --export intercom
python multi_source_rag.py --export helpcenter
python multi_source_rag.py --export confluence
python multi_source_rag.py --export veeva
python multi_source_rag.py --export pdf
python multi_source_rag.py --export community
```

## Dependencies
```bash
pip install slack-bolt anthropic chromadb sentence-transformers python-dotenv requests beautifulsoup4
```

## Notes
- ChromaDB uses local embeddings (sentence-transformers) - no external embedding API needed
- Socket Mode = no public URL required, connects outbound to Slack
- Bot needs to be invited to channels it monitors

## Sync Suggestions to Git

Approved suggestions are stored in the AWS environment. To sync them back to the repo:

### On AWS (daily cron job)
```bash
# Export community collection and commit to git
python multi_source_rag.py --export community
git add community_backup.json pending_suggestions.json
git commit -m "Sync suggestions $(date +%Y-%m-%d)" || true
git push origin main
```

### Cron Setup
```bash
# Add to crontab -e (runs daily at 2am)
0 2 * * * cd /path/to/Magic-Answer && python multi_source_rag.py --export community && git add community_backup.json pending_suggestions.json && git commit -m "Sync suggestions $(date +\%Y-\%m-\%d)" && git push origin main
```

### Pull Locally
```bash
git pull origin main
python multi_source_rag.py --import community
```
