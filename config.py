"""
MagicAnswer Configuration
=========================
Central configuration for all settings.
Copy .env.example to .env and fill in your values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# API KEYS
# =============================================================================
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
INTERCOM_ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN")

# Confluence (Atlassian Cloud)
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")  # e.g., https://shaman.atlassian.net/wiki
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

# =============================================================================
# PATHS
# =============================================================================
DB_PATH = os.getenv("DB_PATH", "./knowledge_base")
VIDEO_TRANSCRIPTS_PATH = os.getenv("VIDEO_TRANSCRIPTS_PATH", "./transcripts")

# =============================================================================
# SLACK SETTINGS
# =============================================================================
SLACK_CHANNELS = [
    "product-questions",
    # Add more channels as needed
]
SLACK_DAYS_TO_FETCH = 365
SLACK_MAX_MESSAGES_PER_CHANNEL = 2000

# =============================================================================
# INTERCOM SETTINGS
# =============================================================================
INTERCOM_DAYS_TO_FETCH = 180

# =============================================================================
# CONFLUENCE SETTINGS
# =============================================================================
CONFLUENCE_SPACES = ["ADMIN", "Product"]  # Space keys to index (update with accessible spaces)
CONFLUENCE_MAX_PAGES = 500

# =============================================================================
# LLM SETTINGS
# =============================================================================
LLM_MODEL = "claude-sonnet-4-20250514"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# =============================================================================
# CACHE SETTINGS
# =============================================================================
CACHE_TTL_SECONDS = 3600  # 1 hour

# =============================================================================
# ROUTING SETTINGS
# =============================================================================
BUG_REPORT_URL = os.getenv("BUG_REPORT_URL", "https://jira.example.com/create")
ENHANCEMENT_URL = os.getenv("ENHANCEMENT_URL", "https://productboard.example.com")
CONFIDENCE_THRESHOLD = 0.8  # Minimum confidence to auto-route bugs/enhancements
