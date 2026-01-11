"""
Multi-Source RAG System
=======================
Knowledge base combining:
- Slack channels
- Intercom Help Center (public articles)
- Intercom Conversations (support tickets)
- Confluence pages

SETUP
-----
pip install slack-sdk anthropic python-dotenv chromadb sentence-transformers requests beautifulsoup4

INTERCOM SETUP
--------------
1. Go to Intercom Settings → Integrations → Developer Hub
2. Create a new app or select existing
3. Go to Authentication → create/copy Access Token
4. Add to .env file:
   INTERCOM_ACCESS_TOKEN=your_token_here

Required token scopes:
- Read and list help center content
- Read conversations

USAGE
-----
python multi_source_rag.py --sync-all           # Sync everything
python multi_source_rag.py --sync-slack         # Sync only Slack (uses SLACK_CHANNELS env or default)
python multi_source_rag.py --sync-slack --channels product-questions,support   # Sync specific channels
python multi_source_rag.py --sync-helpcenter    # Sync only Help Center
python multi_source_rag.py --sync-intercom      # Sync only Intercom conversations
python multi_source_rag.py --sync-veeva         # Sync only Veeva docs
python multi_source_rag.py --sync-pdfs          # Sync PDFs from content_input/pdfs/
python multi_source_rag.py --sync-manual        # Sync manual docs from content_input/manual_docs/
python multi_source_rag.py --sync-confluence    # Sync Confluence pages
python multi_source_rag.py --sync-customer      # Sync all customer docs
python multi_source_rag.py --sync-customer novartis  # Sync specific customer docs
python multi_source_rag.py                      # Interactive Q&A mode

CONTENT INPUT FOLDERS
---------------------
All input documents go in content_input/:
  content_input/pdfs/          - PDF documents
  content_input/manual_docs/   - Manual markdown/HTML docs
  content_input/customer_docs/ - Customer-specific docs (per customer subfolder)

CUSTOMER-SPECIFIC MODE
----------------------
Configure customers in customer_config.py with their Slack channel IDs.
When users ask questions in a customer-specific channel, MagicAnswer will
automatically include that customer's documentation in search results.

SLACK CHANNELS
--------------
Configure channels to sync in three ways (priority order):
1. CLI: --channels product-questions,support,general
2. Env var: SLACK_CHANNELS=product-questions,support,general
3. Default: product-questions (hardcoded fallback)
"""

import os
import json
import argparse
import hashlib
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic
import chromadb
from chromadb.utils import embedding_functions

# Import customer configuration
try:
    from customer_config import CUSTOMERS, get_customer_by_channel, get_customer_by_channel_name, get_all_customer_keys
except ImportError:
    CUSTOMERS = {}
    def get_customer_by_channel(channel_id): return None
    def get_customer_by_channel_name(channel_name): return None
    def get_all_customer_keys(): return []

# Load environment variables
load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

# Slack settings - can be overridden via env var or CLI
# Set SLACK_CHANNELS env var as comma-separated list: "product-questions,support,general"
SLACK_CHANNELS_DEFAULT = ["product-questions"]
SLACK_CHANNELS = os.environ.get("SLACK_CHANNELS", "").split(",") if os.environ.get("SLACK_CHANNELS") else SLACK_CHANNELS_DEFAULT
SLACK_CHANNELS = [c.strip() for c in SLACK_CHANNELS if c.strip()]  # Clean up whitespace
SLACK_DAYS_TO_FETCH = 365
SLACK_MAX_MESSAGES_PER_CHANNEL = 2000

# Intercom Help Center - now uses API directly (no URL needed)

# Intercom Conversations settings
INTERCOM_DAYS_TO_FETCH = 365  # 12 months

# Database (use env var for Railway deployment)
DB_PATH = os.getenv("DB_PATH", "./knowledge_base")


# =============================================================================
# RAG SYSTEM
# =============================================================================

class MultiSourceRAG:
    # Write lock to prevent concurrent write corruption
    _write_lock = None

    @classmethod
    def get_write_lock(cls):
        import threading
        if cls._write_lock is None:
            cls._write_lock = threading.Lock()
        return cls._write_lock

    def __init__(self):
        from chromadb.config import Settings
        self.chroma_client = chromadb.PersistentClient(
            path=DB_PATH,
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=False,  # Prevent accidental resets
            )
        )

        # Use sentence-transformers for embeddings (runs locally)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        # Separate collections for different sources
        self.collections = {
            "slack": self.chroma_client.get_or_create_collection(
                name="slack_messages",
                embedding_function=self.embedding_fn
            ),
            "helpcenter": self.chroma_client.get_or_create_collection(
                name="helpcenter_articles",
                embedding_function=self.embedding_fn
            ),
            "intercom": self.chroma_client.get_or_create_collection(
                name="intercom_conversations",
                embedding_function=self.embedding_fn
            ),
            "veeva": self.chroma_client.get_or_create_collection(
                name="veeva_docs",
                embedding_function=self.embedding_fn
            ),
            "pdf": self.chroma_client.get_or_create_collection(
                name="pdf_documents",
                embedding_function=self.embedding_fn
            ),
            "manual": self.chroma_client.get_or_create_collection(
                name="manual_documents",
                embedding_function=self.embedding_fn
            ),
            "confluence": self.chroma_client.get_or_create_collection(
                name="confluence_pages",
                embedding_function=self.embedding_fn
            ),
            "video": self.chroma_client.get_or_create_collection(
                name="video_transcripts",
                embedding_function=self.embedding_fn
            ),
            "features": self.chroma_client.get_or_create_collection(
                name="shaman_features",
                embedding_function=self.embedding_fn
            ),
            "community": self.chroma_client.get_or_create_collection(
                name="community_contributions",
                embedding_function=self.embedding_fn
            ),
        }

        # Initialize clients lazily
        self._slack_client = None
        self._anthropic_client = None
        self._intercom_token = None

        # Customer collections cache
        self._customer_collections = {}

    def get_customer_collection(self, customer_key: str):
        """Get or create a collection for customer-specific documents.

        Args:
            customer_key: Customer identifier (e.g., "novartis", "gsk")

        Returns:
            ChromaDB collection for the customer
        """
        if customer_key not in self._customer_collections:
            collection_name = f"customer_{customer_key}"
            self._customer_collections[customer_key] = self.chroma_client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_fn
            )
        return self._customer_collections[customer_key]

    @property
    def slack_client(self) -> WebClient:
        if self._slack_client is None:
            token = os.getenv("SLACK_BOT_TOKEN")
            if not token:
                raise ValueError("SLACK_BOT_TOKEN not found in .env")
            self._slack_client = WebClient(token=token)
        return self._slack_client

    @property
    def anthropic_client(self) -> anthropic.Anthropic:
        if self._anthropic_client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in .env")
            self._anthropic_client = anthropic.Anthropic(api_key=api_key)
        return self._anthropic_client

    @property
    def intercom_token(self) -> str:
        if self._intercom_token is None:
            self._intercom_token = os.getenv("INTERCOM_ACCESS_TOKEN", "")
        return self._intercom_token

    def _intercom_request(self, endpoint: str, params: dict = None) -> dict:
        """Make authenticated request to Intercom API."""
        if not self.intercom_token:
            raise ValueError("INTERCOM_ACCESS_TOKEN not found in .env")

        headers = {
            "Authorization": f"Bearer {self.intercom_token}",
            "Accept": "application/json",
            "Intercom-Version": "2.10"
        }

        url = f"https://api.intercom.io/{endpoint}"
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 429:  # Rate limited
            retry_after = int(response.headers.get("Retry-After", 10))
            print(f"   Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            return self._intercom_request(endpoint, params)

        response.raise_for_status()
        return response.json()

    # =========================================================================
    # SLACK SYNC
    # =========================================================================

    def _get_slack_user_names(self, user_ids: set) -> dict:
        """Fetch display names for Slack user IDs."""
        user_names = {}
        for user_id in user_ids:
            try:
                result = self.slack_client.users_info(user=user_id)
                profile = result["user"]["profile"]
                user_names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
            except SlackApiError:
                user_names[user_id] = user_id
        return user_names

    def _find_slack_channel_id(self, channel_name: str) -> str:
        """Find Slack channel ID by name."""
        try:
            result = self.slack_client.conversations_list(types="public_channel,private_channel")
            for channel in result["channels"]:
                if channel["name"] == channel_name:
                    return channel["id"]

            while result.get("response_metadata", {}).get("next_cursor"):
                result = self.slack_client.conversations_list(
                    types="public_channel,private_channel",
                    cursor=result["response_metadata"]["next_cursor"]
                )
                for channel in result["channels"]:
                    if channel["name"] == channel_name:
                        return channel["id"]

            raise ValueError(f"Channel '{channel_name}' not found")
        except SlackApiError as e:
            raise ValueError(f"Error finding channel: {e.response['error']}")

    def sync_slack(self, channels: list = None):
        """Sync Slack channels to knowledge base.

        Args:
            channels: Optional list of channel names. If not provided, uses SLACK_CHANNELS config.
        """
        print("\n" + "=" * 60)
        print("📱 SYNCING SLACK")
        print("=" * 60)

        channels_to_sync = channels if channels else SLACK_CHANNELS
        print(f"   Channels: {', '.join(channels_to_sync)}")

        for channel_name in channels_to_sync:
            try:
                print(f"\n📥 #{channel_name}...")
                channel_id = self._find_slack_channel_id(channel_name)

                # Fetch messages
                messages = []
                oldest = datetime.now() - timedelta(days=SLACK_DAYS_TO_FETCH)
                oldest_ts = oldest.timestamp()

                result = self.slack_client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest_ts),
                    limit=200
                )
                messages.extend(result["messages"])

                while result.get("has_more") and len(messages) < SLACK_MAX_MESSAGES_PER_CHANNEL:
                    result = self.slack_client.conversations_history(
                        channel=channel_id,
                        oldest=str(oldest_ts),
                        limit=200,
                        cursor=result["response_metadata"]["next_cursor"]
                    )
                    messages.extend(result["messages"])

                print(f"   Fetched {len(messages)} messages")

                # Get user IDs and fetch threads
                user_ids = set()
                for msg in messages:
                    if msg.get("user"):
                        user_ids.add(msg["user"])
                    if msg.get("reply_count", 0) > 0:
                        try:
                            replies = self.slack_client.conversations_replies(
                                channel=channel_id, ts=msg["ts"]
                            )["messages"][1:]
                            msg["_replies"] = replies
                            for r in replies:
                                if r.get("user"):
                                    user_ids.add(r["user"])
                        except SlackApiError:
                            msg["_replies"] = []

                user_names = self._get_slack_user_names(user_ids)

                # Index messages
                documents, metadatas, ids = [], [], []
                for msg in messages:
                    if msg.get("subtype"):
                        continue

                    msg_id = f"slack_{channel_name}_{msg['ts']}"

                    # Skip if exists
                    existing = self.collections["slack"].get(ids=[msg_id])
                    if existing and existing['ids']:
                        continue

                    user = user_names.get(msg.get("user", ""), "Unknown")
                    text = msg.get("text", "")
                    ts = datetime.fromtimestamp(float(msg["ts"]))

                    for user_id, name in user_names.items():
                        text = text.replace(f"<@{user_id}>", f"@{name}")

                    doc_parts = [f"[{ts.strftime('%Y-%m-%d')}] {user}: {text}"]
                    for reply in msg.get("_replies", []):
                        reply_user = user_names.get(reply.get("user", ""), "Unknown")
                        reply_text = reply.get("text", "")
                        for user_id, name in user_names.items():
                            reply_text = reply_text.replace(f"<@{user_id}>", f"@{name}")
                        doc_parts.append(f"  → {reply_user}: {reply_text}")

                    documents.append("\n".join(doc_parts))
                    metadatas.append({
                        "source": "slack",
                        "channel": channel_name,
                        "user": user,
                        "date": ts.strftime('%Y-%m-%d'),
                    })
                    ids.append(msg_id)

                # Add to collection
                if documents:
                    for i in range(0, len(documents), 100):
                        self.collections["slack"].add(
                            documents=documents[i:i + 100],
                            metadatas=metadatas[i:i + 100],
                            ids=ids[i:i + 100]
                        )
                    print(f"   ✓ Indexed {len(documents)} new messages")
                else:
                    print("   ✓ No new messages")

            except Exception as e:
                print(f"   ⚠ Error: {e}")

        print(f"\n📊 Slack collection: {self.collections['slack'].count()} messages")

    # =========================================================================
    # HELP CENTER SYNC (via Intercom API)
    # =========================================================================

    def sync_helpcenter(self):
        """Sync Help Center articles via Intercom API."""
        print("\n" + "=" * 60)
        print("📚 SYNCING HELP CENTER (via Intercom API)")
        print("=" * 60)

        if not self.intercom_token:
            print("   ⚠ INTERCOM_ACCESS_TOKEN not found in .env")
            return

        try:
            # Fetch all articles with cursor-based pagination
            print("\n   Fetching articles from Intercom API...")
            articles_list = []
            page = 1
            starting_after = None

            while True:
                print(f"   Page {page}...")

                params = {"per_page": 50, "page": page}

                data = self._intercom_request("articles", params)

                if not data:
                    break

                items = data.get("data", [])
                if not items:
                    break

                articles_list.extend(items)

                # Check if there's a next page
                pages = data.get("pages", {})
                total_pages = pages.get("total_pages", 1)

                print(f"      → Got {len(items)} items (page {page} of {total_pages})")

                if page >= total_pages:
                    break

                page += 1

            # Filter to published only
            published = [a for a in articles_list if a.get("state") == "published"]
            print(f"   Found {len(published)} published articles (of {len(articles_list)} total)")

            # Fetch full content and index
            documents, metadatas, ids = [], [], []

            for i, article in enumerate(published):
                article_id = f"helpcenter_{article['id']}"

                # Skip if exists
                existing = self.collections["helpcenter"].get(ids=[article_id])
                if existing and existing['ids']:
                    continue

                # Fetch full article
                try:
                    detail = self._intercom_request(f"articles/{article['id']}")

                    title = article.get("title", "Untitled")
                    body = detail.get("body", "")

                    # Clean HTML
                    if body and "<" in body:
                        soup = BeautifulSoup(body, 'html.parser')
                        body = soup.get_text(separator='\n', strip=True)

                    if not body or len(body) < 50:
                        continue

                    doc = f"# {title}\n\n{body}"

                    documents.append(doc)
                    metadatas.append({
                        "source": "helpcenter",
                        "title": title,
                        "url": article.get("url", ""),
                        "article_id": article["id"],
                    })
                    ids.append(article_id)

                    if (i + 1) % 20 == 0:
                        print(f"   Processed {i + 1}/{len(published)}...")

                    time.sleep(0.1)

                except Exception as e:
                    print(f"   ⚠ Error fetching article {article['id']}: {e}")
                    continue

            # Add to collection
            if documents:
                for i in range(0, len(documents), 100):
                    self.collections["helpcenter"].add(
                        documents=documents[i:i + 100],
                        metadatas=metadatas[i:i + 100],
                        ids=ids[i:i + 100]
                    )
                print(f"   ✓ Indexed {len(documents)} new articles")
            else:
                print("   ✓ No new articles")

        except Exception as e:
            print(f"   ⚠ Error: {e}")

        print(f"\n📊 Help Center collection: {self.collections['helpcenter'].count()} articles")

    # =========================================================================
    # INTERCOM CONVERSATIONS SYNC
    # =========================================================================

    def sync_intercom_conversations(self):
        """Sync Intercom conversations to knowledge base."""
        print("\n" + "=" * 60)
        print("💬 SYNCING INTERCOM CONVERSATIONS")
        print("=" * 60)

        if not self.intercom_token:
            print("   ⚠ INTERCOM_ACCESS_TOKEN not found in .env")
            return

        try:
            # Fetch ALL conversations with cursor-based pagination, then filter by date
            print(f"\n   Fetching all conversations (will filter to last {INTERCOM_DAYS_TO_FETCH} days)...")
            all_conversations = []
            page = 1
            cutoff = datetime.now() - timedelta(days=INTERCOM_DAYS_TO_FETCH)
            cutoff_ts = int(cutoff.timestamp())
            starting_after = None

            while True:
                print(f"   Page {page}...", end=" ")

                params = {"per_page": 50}
                if starting_after:
                    params["starting_after"] = starting_after

                data = self._intercom_request("conversations", params)

                if not data:
                    print("(error)")
                    break

                convos = data.get("conversations", [])
                if not convos:
                    print("(empty)")
                    break

                all_conversations.extend(convos)
                print(f"got {len(convos)} (total: {len(all_conversations)})")

                # Get cursor for next page
                pages = data.get("pages", {})
                next_page = pages.get("next")

                if next_page and isinstance(next_page, dict):
                    starting_after = next_page.get("starting_after")
                    if not starting_after:
                        break
                else:
                    break

                page += 1

            # Now filter by date
            conversations = [
                conv for conv in all_conversations
                if conv.get("created_at", 0) >= cutoff_ts
            ]

            print(f"   Total fetched: {len(all_conversations)}")
            print(f"   Within date range: {len(conversations)}")

        except Exception as e:
            print(f"   ⚠ Error fetching conversations: {e}")
            return

        # Fetch conversation details and index
        documents, metadatas, ids = [], [], []

        for i, conv in enumerate(conversations):
            conv_id = f"intercom_{conv['id']}"

            # Skip if exists
            existing = self.collections["intercom"].get(ids=[conv_id])
            if existing and existing['ids']:
                continue

            try:
                # Fetch full conversation
                detail = self._intercom_request(f"conversations/{conv['id']}")

                # Build conversation text
                parts = []

                # Get customer info
                source = detail.get("source", {})
                customer = source.get("author", {})
                customer_name = customer.get("name", "Customer")

                # Initial message
                if source.get("body"):
                    body = BeautifulSoup(source["body"], 'html.parser').get_text(strip=True)
                    parts.append(f"Customer ({customer_name}): {body}")

                # Conversation parts (replies)
                for part in detail.get("conversation_parts", {}).get("conversation_parts", []):
                    author = part.get("author", {})
                    author_name = author.get("name", "Agent")
                    author_type = author.get("type", "")

                    body = part.get("body", "")
                    if body:
                        body = BeautifulSoup(body, 'html.parser').get_text(strip=True)

                        if author_type == "user":
                            parts.append(f"Customer ({author_name}): {body}")
                        else:
                            parts.append(f"Agent ({author_name}): {body}")

                if not parts:
                    continue

                # Create document
                created = datetime.fromtimestamp(conv.get("created_at", 0))
                doc = f"[Intercom conversation - {created.strftime('%Y-%m-%d')}]\n\n" + "\n\n".join(parts)

                documents.append(doc)
                metadatas.append({
                    "source": "intercom",
                    "conversation_id": conv["id"],
                    "date": created.strftime('%Y-%m-%d'),
                    "state": conv.get("state", ""),
                })
                ids.append(conv_id)

                if (i + 1) % 20 == 0:
                    print(f"   Processed {i + 1}/{len(conversations)}...")

                time.sleep(0.2)  # Rate limiting

            except Exception as e:
                print(f"   ⚠ Error processing conversation {conv['id']}: {e}")
                continue

        # Add to collection
        if documents:
            for i in range(0, len(documents), 100):
                self.collections["intercom"].add(
                    documents=documents[i:i + 100],
                    metadatas=metadatas[i:i + 100],
                    ids=ids[i:i + 100]
                )
            print(f"   ✓ Indexed {len(documents)} new conversations")
        else:
            print("   ✓ No new conversations")

        print(f"\n📊 Intercom collection: {self.collections['intercom'].count()} conversations")

    # =========================================================================
    # VEEVA HELP CENTER SYNC
    # =========================================================================

    def sync_veeva(self, from_file: bool = True):
        """Sync Veeva Help Center articles to knowledge base.

        Args:
            from_file: If True, load from veeva_helpcenter.json. If False, scrape fresh.
        """
        print("\n" + "=" * 60)
        print("📗 SYNCING VEEVA HELP CENTER")
        print("=" * 60)

        articles = []

        # Try loading from JSON file first
        json_path = os.path.join(os.path.dirname(__file__), "veeva_helpcenter.json")

        if from_file and os.path.exists(json_path):
            print(f"   Loading from {json_path}...")
            try:
                with open(json_path, 'r') as f:
                    articles = json.load(f)
                print(f"   Found {len(articles)} articles in JSON file")
            except Exception as e:
                print(f"   ⚠ Error loading JSON: {e}")
                articles = []

        # If no articles from file, try scraping
        if not articles:
            print("   Scraping Veeva documentation...")
            try:
                from veeva_scraper import VeevaHelpScraper
                scraper = VeevaHelpScraper()
                articles = scraper.scrape_all()
            except ImportError:
                print("   ⚠ veeva_scraper.py not found. Place it in the same directory.")
                return
            except Exception as e:
                print(f"   ⚠ Error scraping: {e}")
                return

        if not articles:
            print("   ⚠ No articles found")
            return

        # Index articles
        documents, metadatas, ids = [], [], []

        for article in articles:
            article_id = f"veeva_{hashlib.md5(article['url'].encode()).hexdigest()}"

            # Skip if exists
            existing = self.collections["veeva"].get(ids=[article_id])
            if existing and existing['ids']:
                continue

            doc = f"# {article['title']}\n\n{article['content']}"

            documents.append(doc)
            metadatas.append({
                "source": "veeva",
                "title": article["title"],
                "url": article["url"],
                "section": article.get("section", "general"),
            })
            ids.append(article_id)

        # Add to collection
        if documents:
            for i in range(0, len(documents), 100):
                self.collections["veeva"].add(
                    documents=documents[i:i + 100],
                    metadatas=metadatas[i:i + 100],
                    ids=ids[i:i + 100]
                )
            print(f"   ✓ Indexed {len(documents)} new Veeva articles")
        else:
            print("   ✓ No new articles (all already indexed)")

        print(f"\n📊 Veeva collection: {self.collections['veeva'].count()} articles")

    # =========================================================================
    # PDF SYNC
    # =========================================================================

    def sync_pdfs(self, pdf_folder: str = None, use_vision: bool = False):
        """Sync PDF documents to knowledge base.

        Args:
            pdf_folder: Path to folder containing PDFs. Defaults to ./content_input/pdfs/
            use_vision: If True, use Claude Vision to extract descriptions from images/screenshots.
                       This creates richer embeddings for tutorial-style PDFs (like Arcade).
        """
        print("\n" + "=" * 60)
        if use_vision:
            print("📄 SYNCING PDF DOCUMENTS (with Vision)")
        else:
            print("📄 SYNCING PDF DOCUMENTS")
        print("=" * 60)

        try:
            from ingest.pdf_ingestor import ingest_pdfs
        except ImportError as e:
            print(f"   ⚠ Error importing PDF ingestor: {e}")
            print("   Make sure pdfplumber is installed: pip install pdfplumber")
            return

        # Get documents from PDFs
        documents = ingest_pdfs(pdf_folder, use_vision=use_vision)

        if not documents:
            print("   ⚠ No PDF documents to index")
            return

        # Filter out already indexed documents
        new_docs = []
        for doc in documents:
            existing = self.collections["pdf"].get(ids=[doc["id"]])
            if not existing or not existing['ids']:
                new_docs.append(doc)

        if not new_docs:
            print("   ✓ No new PDF documents (all already indexed)")
            print(f"\n📊 PDF collection: {self.collections['pdf'].count()} chunks")
            return

        # Add to collection in batches
        for i in range(0, len(new_docs), 100):
            batch = new_docs[i:i + 100]
            self.collections["pdf"].add(
                documents=[d["content"] for d in batch],
                metadatas=[d["metadata"] for d in batch],
                ids=[d["id"] for d in batch]
            )

        print(f"   ✓ Indexed {len(new_docs)} new PDF chunks")
        print(f"\n📊 PDF collection: {self.collections['pdf'].count()} chunks")

    # =========================================================================
    # MANUAL DOCS SYNC
    # =========================================================================

    def sync_manual(self, folder: str = None):
        """Sync manually added documents to knowledge base.

        Args:
            folder: Path to folder containing manual docs. Defaults to ./content_input/manual_docs/
        """
        print("\n" + "=" * 60)
        print("📝 SYNCING MANUAL DOCUMENTS")
        print("=" * 60)

        try:
            from ingest.manual_ingestor import ingest_manual_docs
        except ImportError as e:
            print(f"   ⚠ Error importing manual ingestor: {e}")
            return

        # Get documents
        documents = ingest_manual_docs(folder)

        if not documents:
            print("   ⚠ No manual documents to index")
            return

        # Filter out already indexed documents
        new_docs = []
        for doc in documents:
            existing = self.collections["manual"].get(ids=[doc["id"]])
            if not existing or not existing['ids']:
                new_docs.append(doc)

        if not new_docs:
            print("   ✓ No new manual documents (all already indexed)")
            print(f"\n📊 Manual collection: {self.collections['manual'].count()} chunks")
            return

        # Add to collection in batches
        for i in range(0, len(new_docs), 100):
            batch = new_docs[i:i + 100]
            self.collections["manual"].add(
                documents=[d["content"] for d in batch],
                metadatas=[d["metadata"] for d in batch],
                ids=[d["id"] for d in batch]
            )

        print(f"   ✓ Indexed {len(new_docs)} new manual document chunks")
        print(f"\n📊 Manual collection: {self.collections['manual'].count()} chunks")

    # =========================================================================
    # CONFLUENCE SYNC
    # =========================================================================

    def sync_confluence(self):
        """Sync Confluence pages to knowledge base."""
        print("\n" + "=" * 60)
        print("📘 SYNCING CONFLUENCE")
        print("=" * 60)

        try:
            from ingest.confluence import ConfluenceIngestor
            from services.vector_store import VectorStore
        except ImportError as e:
            print(f"   ⚠ Error importing Confluence ingestor: {e}")
            return

        try:
            # Create a minimal vector store wrapper for the ingestor
            class CollectionWrapper:
                def __init__(self, collection):
                    self.collection = collection

                def get(self, ids):
                    return self.collection.get(ids=ids)

                def add(self, documents, metadatas, ids):
                    self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

            wrapper = CollectionWrapper(self.collections["confluence"])

            # Initialize ingestor
            ingestor = ConfluenceIngestor(wrapper)

            # Fetch and index documents
            documents, metadatas, ids = [], [], []

            for doc in ingestor.fetch_documents():
                # Skip if exists
                existing = self.collections["confluence"].get(ids=[doc.id])
                if existing and existing['ids']:
                    continue

                documents.append(doc.content)
                metadatas.append(doc.metadata)
                ids.append(doc.id)

            # Add to collection
            if documents:
                for i in range(0, len(documents), 100):
                    self.collections["confluence"].add(
                        documents=documents[i:i + 100],
                        metadatas=metadatas[i:i + 100],
                        ids=ids[i:i + 100]
                    )
                print(f"   ✓ Indexed {len(documents)} new Confluence page chunks")
            else:
                print("   ✓ No new pages (all already indexed)")

        except ValueError as e:
            print(f"   ⚠ {e}")
            return
        except Exception as e:
            print(f"   ⚠ Error: {e}")
            return

        print(f"\n📊 Confluence collection: {self.collections['confluence'].count()} chunks")

    def sync_video(self):
        """Sync video transcripts to knowledge base."""
        print("\n" + "=" * 60)
        print("🎬 SYNCING VIDEO TRANSCRIPTS")
        print("=" * 60)

        try:
            from ingest.video_transcripts import VideoIngestor
        except ImportError as e:
            print(f"   ⚠ Error importing Video ingestor: {e}")
            return

        try:
            # Create a minimal wrapper for the ingestor
            class CollectionWrapper:
                def __init__(self, collection):
                    self.collection = collection

                def get(self, ids):
                    return self.collection.get(ids=ids)

                def add(self, documents, metadatas, ids):
                    self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

            wrapper = CollectionWrapper(self.collections["video"])

            # Initialize ingestor
            ingestor = VideoIngestor(wrapper)

            # Fetch and index documents
            documents, metadatas, ids = [], [], []

            for doc in ingestor.fetch_documents():
                # Skip if exists
                existing = self.collections["video"].get(ids=[doc.id])
                if existing and existing['ids']:
                    continue

                documents.append(doc.content)
                metadatas.append(doc.metadata)
                ids.append(doc.id)

            if not documents:
                print("   ✓ No new video transcripts to index")
            else:
                # Add in batches
                for i in range(0, len(documents), 100):
                    batch_docs = documents[i:i + 100]
                    batch_metas = metadatas[i:i + 100]
                    batch_ids = ids[i:i + 100]

                    self.collections["video"].add(
                        documents=batch_docs,
                        metadatas=batch_metas,
                        ids=batch_ids
                    )

                print(f"   ✓ Indexed {len(documents)} new transcript chunks")

        except Exception as e:
            print(f"   ⚠ Error: {e}")
            return

        print(f"\n📊 Video collection: {self.collections['video'].count()} chunks")

    def sync_features(self, json_file: str = "features_enriched.json", force: bool = False):
        """Sync enriched features JSON to knowledge base.

        Args:
            json_file: Path to the enriched features JSON file
            force: If True, delete all existing features and re-index
        """
        print("\n" + "=" * 60)
        print("📋 SYNCING SHAMAN FEATURES")
        print("=" * 60)

        if not os.path.exists(json_file):
            print(f"   ⚠ File not found: {json_file}")
            print("   Run: python enrich_features.py <your_excel_file.xlsx>")
            return

        try:
            with open(json_file, 'r') as f:
                features = json.load(f)

            print(f"   Found {len(features)} features")

            # Force re-index: clear existing features
            if force:
                existing_count = self.collections["features"].count()
                if existing_count > 0:
                    print(f"   🔄 Force mode: clearing {existing_count} existing features...")
                    # Get all IDs and delete
                    all_items = self.collections["features"].get()
                    if all_items and all_items['ids']:
                        self.collections["features"].delete(ids=all_items['ids'])
                    print("   ✓ Cleared existing features")

            documents, metadatas, ids = [], [], []

            for feature in features:
                feature_id = f"feature_{feature.get('id', feature.get('name', ''))}"

                # Skip if exists (unless force mode already cleared)
                if not force:
                    existing = self.collections["features"].get(ids=[feature_id])
                    if existing and existing['ids']:
                        continue

                # Build document content - include feature name multiple times for better search
                feature_name = feature.get('name', 'Feature')
                content = f"# {feature_name}\n\n"
                content += f"Feature Name: {feature_name}\n\n"
                content += feature.get('enriched_description', '')

                # Add original data as context
                if feature.get('original_data'):
                    content += "\n\nDetails:\n"
                    for k, v in feature['original_data'].items():
                        if v:
                            content += f"- {k}: {v}\n"

                # Add feature name again at the end for better embedding
                content += f"\n\nThis feature is called {feature_name}."

                documents.append(content)
                metadatas.append({
                    "source": "features",
                    "title": feature.get('name', 'Feature'),
                    "type": "feature",
                    **feature.get('metadata', {})
                })
                ids.append(feature_id)

            if not documents:
                print("   ✓ No new features to index")
            else:
                # Add in batches
                for i in range(0, len(documents), 100):
                    batch_docs = documents[i:i + 100]
                    batch_metas = metadatas[i:i + 100]
                    batch_ids = ids[i:i + 100]

                    self.collections["features"].add(
                        documents=batch_docs,
                        metadatas=batch_metas,
                        ids=batch_ids
                    )

                print(f"   ✓ Indexed {len(documents)} new features")

        except Exception as e:
            print(f"   ⚠ Error: {e}")
            return

        print(f"\n📊 Features collection: {self.collections['features'].count()} items")

    def sync_context(self, context_file: str = "shaman_context.md"):
        """Sync Shaman platform context document to knowledge base.

        Args:
            context_file: Path to the context markdown file
        """
        print("\n" + "=" * 60)
        print("📖 SYNCING SHAMAN CONTEXT DOCUMENT")
        print("=" * 60)

        if not os.path.exists(context_file):
            print(f"   ⚠ File not found: {context_file}")
            return

        try:
            with open(context_file, 'r') as f:
                content = f.read()

            print(f"   Found context document ({len(content)} chars)")

            # Create a unique ID based on file content hash
            content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
            doc_id = f"context_shaman_{content_hash}"

            # Check if already indexed (same content)
            existing = self.collections["manual"].get(ids=[doc_id])
            if existing and existing['ids']:
                print("   ✓ Context document already indexed (unchanged)")
                print(f"\n📊 Manual collection: {self.collections['manual'].count()} chunks")
                return

            # Remove old context documents before adding new one
            old_docs = self.collections["manual"].get(
                where={"type": "platform_context"}
            )
            if old_docs and old_docs['ids']:
                print(f"   Removing {len(old_docs['ids'])} old context chunks...")
                self.collections["manual"].delete(ids=old_docs['ids'])

            # Chunk the document for better retrieval
            # Split by sections (## headers)
            import re
            sections = re.split(r'\n(?=## )', content)

            documents, metadatas, ids = [], [], []

            for i, section in enumerate(sections):
                if len(section.strip()) < 50:
                    continue

                # Get section title
                lines = section.strip().split('\n')
                title = lines[0].replace('#', '').strip() if lines else f"Section {i+1}"

                section_id = f"context_shaman_{content_hash}_{i}"

                documents.append(section)
                metadatas.append({
                    "source": "manual",
                    "title": f"Shaman Context: {title}",
                    "type": "platform_context",
                    "section": i,
                    "file": context_file
                })
                ids.append(section_id)

            if documents:
                self.collections["manual"].add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
                print(f"   ✓ Indexed {len(documents)} context sections")

        except Exception as e:
            print(f"   ⚠ Error: {e}")
            return

        print(f"\n📊 Manual collection: {self.collections['manual'].count()} chunks")

    # =========================================================================
    # TERMINOLOGY SYNC
    # =========================================================================

    def sync_terminology(self):
        """Sync terminology glossary to manual collection for searchability.

        Loads terms from content_input/terminology.json and indexes them
        so users can ask "what is X?" questions.
        """
        print("\n" + "=" * 60)
        print("📖 SYNCING TERMINOLOGY GLOSSARY")
        print("=" * 60)

        terminology_path = "content_input/terminology.json"
        if not os.path.exists(terminology_path):
            print(f"   ⚠ File not found: {terminology_path}")
            return

        try:
            with open(terminology_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            print(f"   ⚠ Error loading terminology: {e}")
            return

        glossary = data.get("glossary", [])
        if not glossary:
            print("   ⚠ No glossary terms found")
            return

        print(f"   Found {len(glossary)} glossary terms")

        documents = []
        metadatas = []
        ids = []

        for item in glossary:
            term = item.get("term", "")
            description = item.get("description", "")
            acronym = item.get("acronym", "")

            if not term or not description:
                continue

            # Create searchable content
            content = f"# {term}\n\n"
            if acronym:
                content += f"Acronym: {acronym}\n\n"
            content += f"{description}"

            # Generate unique ID
            term_slug = term.lower().replace(" ", "_").replace("(", "").replace(")", "")[:50]
            content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
            doc_id = f"terminology_{term_slug}_{content_hash}"

            # Skip if already indexed with same content
            existing = self.collections["manual"].get(ids=[doc_id])
            if existing and existing['ids']:
                continue

            documents.append(content)
            metadatas.append({
                "source": "terminology",
                "type": "glossary",
                "term": term,
                "acronym": acronym or ""
            })
            ids.append(doc_id)

        if documents:
            self.collections["manual"].add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            print(f"   ✓ Indexed {len(documents)} new glossary terms")
        else:
            print("   ✓ All glossary terms already indexed")

        print(f"\n📊 Manual collection: {self.collections['manual'].count()} chunks")

    # =========================================================================
    # CUSTOMER DOCS SYNC
    # =========================================================================

    def sync_customer_docs(self, customer_key: str = None):
        """Sync customer-specific documents to their isolated collection.

        Args:
            customer_key: Specific customer to sync. If None, syncs all customers.
        """
        print("\n" + "=" * 60)
        print("🏢 SYNCING CUSTOMER DOCUMENTS")
        print("=" * 60)

        if not CUSTOMERS:
            print("   ⚠ No customers configured in customer_config.py")
            return

        customers_to_sync = [customer_key] if customer_key else get_all_customer_keys()

        for cust_key in customers_to_sync:
            config = CUSTOMERS.get(cust_key)
            if not config:
                print(f"\n⚠ Unknown customer: {cust_key}")
                continue

            docs_folder = config.get("docs_folder", f"content_input/customer_docs/{cust_key}")
            cust_name = config.get("name", cust_key)

            print(f"\n📁 {cust_name} ({docs_folder})...")

            if not os.path.exists(docs_folder):
                print(f"   ⚠ Folder not found: {docs_folder}")
                print(f"   Create it with: mkdir -p {docs_folder}")
                continue

            # Get the customer collection
            collection = self.get_customer_collection(cust_key)

            # Find all markdown and text files in the customer docs folder
            documents, metadatas, ids = [], [], []

            for root, dirs, files in os.walk(docs_folder):
                for filename in files:
                    if not filename.endswith(('.md', '.txt', '.json')):
                        continue

                    filepath = os.path.join(root, filename)
                    relative_path = os.path.relpath(filepath, docs_folder)

                    # Create unique ID based on file path and content hash
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

                    content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
                    doc_id = f"customer_{cust_key}_{relative_path}_{content_hash}"

                    # Skip if already indexed with same content
                    existing = collection.get(ids=[doc_id])
                    if existing and existing['ids']:
                        continue

                    # For JSON files, try to parse and format nicely
                    if filename.endswith('.json'):
                        try:
                            data = json.loads(content)
                            # If it's a list of items, process each
                            if isinstance(data, list):
                                for i, item in enumerate(data):
                                    item_id = f"customer_{cust_key}_{relative_path}_{i}_{content_hash}"
                                    item_content = json.dumps(item, indent=2)
                                    title = item.get('name', item.get('title', f'Item {i+1}'))
                                    documents.append(f"# {title}\n\n{item_content}")
                                    metadatas.append({
                                        "source": f"customer_{cust_key}",
                                        "customer": cust_key,
                                        "file": relative_path,
                                        "title": title,
                                        "type": "config"
                                    })
                                    ids.append(item_id)
                                continue
                            else:
                                content = f"# {filename}\n\n{json.dumps(data, indent=2)}"
                        except json.JSONDecodeError:
                            pass  # Use raw content

                    # For markdown, extract title from first heading if present
                    title = filename
                    if filename.endswith('.md'):
                        lines = content.split('\n')
                        for line in lines:
                            if line.startswith('# '):
                                title = line[2:].strip()
                                break

                    documents.append(content)
                    metadatas.append({
                        "source": f"customer_{cust_key}",
                        "customer": cust_key,
                        "file": relative_path,
                        "title": title,
                        "type": "document"
                    })
                    ids.append(doc_id)

            # Add to collection in batches
            if documents:
                for i in range(0, len(documents), 100):
                    collection.add(
                        documents=documents[i:i + 100],
                        metadatas=metadatas[i:i + 100],
                        ids=ids[i:i + 100]
                    )
                print(f"   ✓ Indexed {len(documents)} new document chunks")
            else:
                print("   ✓ No new documents (all already indexed)")

            print(f"   📊 {cust_name} collection: {collection.count()} items")

    def get_customer_stats(self) -> dict:
        """Get document counts for all customer collections."""
        stats = {}
        for cust_key in get_all_customer_keys():
            try:
                collection = self.get_customer_collection(cust_key)
                stats[cust_key] = collection.count()
            except Exception:
                stats[cust_key] = 0
        return stats

    # =========================================================================
    # EXPORT / BACKUP
    # =========================================================================

    def export_collection(self, source: str, output_path: str = None):
        """Export a collection to JSON for backup.

        Args:
            source: Collection name (slack, intercom, helpcenter, veeva, pdf, manual, confluence)
            output_path: Output file path. Defaults to backups/{source}_backup.json
        """
        if source not in self.collections:
            print(f"   ⚠ Unknown source: {source}")
            return

        collection = self.collections[source]
        count = collection.count()

        if count == 0:
            print(f"   ⚠ {source} collection is empty")
            return

        print(f"\n📦 Exporting {source} ({count} items)...")

        # Get all documents from collection
        result = collection.get(include=["documents", "metadatas"])

        export_data = []
        for i, doc_id in enumerate(result["ids"]):
            export_data.append({
                "id": doc_id,
                "content": result["documents"][i] if result["documents"] else "",
                "metadata": result["metadatas"][i] if result["metadatas"] else {}
            })

        # Save to file - default to backups folder
        if output_path is None:
            os.makedirs("backups", exist_ok=True)
            output_path = f"backups/{source}_backup.json"

        with open(output_path, 'w') as f:
            json.dump(export_data, f, indent=2)

        print(f"   ✓ Exported {len(export_data)} items to {output_path}")

    def import_collection(self, source: str, input_path: str = None):
        """Import a collection from JSON backup.

        Args:
            source: Collection name
            input_path: Input file path. Defaults to backups/{source}_backup.json
        """
        if source not in self.collections:
            print(f"   ⚠ Unknown source: {source}")
            return

        # Check backups folder first, then root
        if input_path is None:
            backup_path = f"backups/{source}_backup.json"
            root_path = f"{source}_backup.json"
            if os.path.exists(backup_path):
                input_path = backup_path
            elif os.path.exists(root_path):
                input_path = root_path
            else:
                print(f"   ⚠ File not found: {backup_path}")
                return

        if not os.path.exists(input_path):
            print(f"   ⚠ File not found: {input_path}")
            return

        print(f"\n📥 Importing {source} from {input_path}...")

        with open(input_path, 'r') as f:
            import_data = json.load(f)

        if not import_data:
            print("   ⚠ No data in backup file")
            return

        # Filter out already existing items
        collection = self.collections[source]
        new_items = []

        for item in import_data:
            existing = collection.get(ids=[item["id"]])
            if not existing or not existing['ids']:
                new_items.append(item)

        if not new_items:
            print(f"   ✓ All {len(import_data)} items already indexed")
            return

        # Add in batches
        for i in range(0, len(new_items), 100):
            batch = new_items[i:i + 100]
            collection.add(
                documents=[item["content"] for item in batch],
                metadatas=[item["metadata"] for item in batch],
                ids=[item["id"] for item in batch]
            )

        print(f"   ✓ Imported {len(new_items)} new items (skipped {len(import_data) - len(new_items)} existing)")
        print(f"\n📊 {source} collection: {collection.count()} items")

    def export_all(self, output_dir: str = "backups"):
        """Export all collections to JSON files for backup.

        Args:
            output_dir: Directory to save backups. Defaults to 'backups'
        """
        os.makedirs(output_dir, exist_ok=True)
        print(f"\n📦 Exporting all collections to {output_dir}/...")

        for source in self.collections:
            output_path = os.path.join(output_dir, f"{source}_backup.json")
            self.export_collection(source, output_path)

        # Also export customer data
        customer_output = os.path.join(output_dir, "customer_data_backup.json")
        self.export_customer_data(customer_output)

        print(f"\n✅ All collections exported to {output_dir}/")

    def export_customer_data(self, output_path: str = "backups/customer_data_backup.json"):
        """Export all customer collections to a single JSON file.

        Args:
            output_path: Output file path. Defaults to backups/customer_data_backup.json
        """
        customer_keys = get_all_customer_keys()
        if not customer_keys:
            print("   ⚠ No customers configured in customer_config.py")
            return

        print(f"\n📦 Exporting customer data...")

        all_customer_data = {}
        total_items = 0

        for cust_key in customer_keys:
            collection = self.get_customer_collection(cust_key)
            count = collection.count()

            if count == 0:
                continue

            result = collection.get(include=["documents", "metadatas"])

            customer_items = []
            for i, doc_id in enumerate(result["ids"]):
                customer_items.append({
                    "id": doc_id,
                    "content": result["documents"][i] if result["documents"] else "",
                    "metadata": result["metadatas"][i] if result["metadatas"] else {}
                })

            all_customer_data[cust_key] = customer_items
            total_items += len(customer_items)
            print(f"   {cust_key}: {len(customer_items)} items")

        if total_items == 0:
            print("   ⚠ No customer data to export")
            return

        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(all_customer_data, f, indent=2)

        print(f"   ✓ Exported {total_items} items to {output_path}")

    def import_customer_data(self, input_path: str = "backups/customer_data_backup.json"):
        """Import all customer collections from a single JSON file.

        Args:
            input_path: Input file path. Defaults to backups/customer_data_backup.json
        """
        # Check backups folder first, then root
        if not os.path.exists(input_path):
            alt_path = os.path.basename(input_path)
            if os.path.exists(alt_path):
                input_path = alt_path
            else:
                print(f"   ⚠ File not found: {input_path}")
                return

        print(f"\n📥 Importing customer data from {input_path}...")

        with open(input_path, 'r') as f:
            all_customer_data = json.load(f)

        if not all_customer_data:
            print("   ⚠ No data in backup file")
            return

        total_imported = 0
        for cust_key, items in all_customer_data.items():
            if not items:
                continue

            collection = self.get_customer_collection(cust_key)

            # Filter out already existing items
            new_items = []
            for item in items:
                existing = collection.get(ids=[item["id"]])
                if not existing or not existing['ids']:
                    new_items.append(item)

            if not new_items:
                print(f"   {cust_key}: All {len(items)} items already indexed")
                continue

            # Add in batches
            for i in range(0, len(new_items), 100):
                batch = new_items[i:i + 100]
                collection.add(
                    documents=[item["content"] for item in batch],
                    metadatas=[item["metadata"] for item in batch],
                    ids=[item["id"] for item in batch]
                )

            total_imported += len(new_items)
            print(f"   {cust_key}: Imported {len(new_items)} new items (skipped {len(items) - len(new_items)} existing)")

        print(f"\n   ✓ Total imported: {total_imported} items")

    def import_all(self, input_dir: str = "backups"):
        """Import all collections from JSON backup files.

        Args:
            input_dir: Directory containing backup files. Defaults to 'backups'
        """
        if not os.path.exists(input_dir):
            print(f"   ⚠ Directory not found: {input_dir}")
            return

        print(f"\n📥 Importing all collections from {input_dir}/...")

        for source in self.collections:
            input_path = os.path.join(input_dir, f"{source}_backup.json")
            if os.path.exists(input_path):
                self.import_collection(source, input_path)
            else:
                print(f"   ⚠ No backup found for {source}")

        print(f"\n✅ Import complete!")
        print(f"\n📊 Knowledge base:")
        for name, collection in self.collections.items():
            count = collection.count()
            if count > 0:
                print(f"   {name}: {count}")

    def veeva_live_search(self, query: str) -> str:
        """Search Veeva help center live via web and answer with Claude."""
        print("   🌐 Searching Veeva Help Center live...")

        # Use Google site-specific search
        search_url = "https://www.google.com/search"
        params = {
            "q": f"site:platform.veevavault.help {query}",
            "num": 5
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract search result URLs
            urls = []
            for a in soup.select('a'):
                href = a.get('href', '')
                if 'platform.veevavault.help' in href and '/url?q=' in href:
                    # Extract actual URL from Google redirect
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    if 'q' in parsed:
                        urls.append(parsed['q'][0])
                elif href.startswith('https://platform.veevavault.help'):
                    urls.append(href)

            urls = list(dict.fromkeys(urls))[:3]  # Dedupe and limit

            if not urls:
                return "Could not find relevant Veeva documentation. Try rephrasing your question."

            # Fetch content from top results
            context_parts = []
            for url in urls:
                try:
                    page = requests.get(url, headers=headers, timeout=10)
                    page_soup = BeautifulSoup(page.text, 'html.parser')

                    # Get title
                    title = page_soup.find('h1')
                    title_text = title.get_text(strip=True) if title else "Veeva Doc"

                    # Get content
                    content = page_soup.find('article') or page_soup.find('main')
                    if content:
                        text = content.get_text(separator='\n', strip=True)[:2000]
                        context_parts.append(f"## {title_text}\nURL: {url}\n\n{text}")

                    time.sleep(0.3)
                except:
                    continue

            if not context_parts:
                return "Found Veeva pages but couldn't extract content. Try visiting: " + urls[0]

            # Ask Claude
            context = "\n\n---\n\n".join(context_parts)

            prompt = f"""Answer this question about Veeva Vault using the documentation below.
Be specific and include relevant URLs.

QUESTION: {query}

VEEVA DOCUMENTATION:
{context}

Provide a clear, helpful answer based on the Veeva documentation above."""

            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}]
            )

            return response.content[0].text

        except Exception as e:
            return f"Error searching Veeva: {e}"

    # =========================================================================
    # QUERY OPTIMIZER
    # =========================================================================

    # Load acronyms from external file for easy maintenance
    @staticmethod
    def load_acronyms() -> dict:
        """Load acronyms from terminology.json file."""
        terminology_path = "content_input/terminology.json"
        if os.path.exists(terminology_path):
            try:
                with open(terminology_path, 'r') as f:
                    data = json.load(f)
                    return data.get("acronyms", {})
            except Exception:
                pass
        # Fallback to minimal set if file not found
        return {
            "CLM": "Closed Loop Marketing CLM presentation",
            "AE": "Approved Email AE RTE",
            "ME": "Mass Email ME HQ Email",
            "MLR": "Medical Legal Regulatory MLR review",
        }

    # Common synonyms in Shaman/Veeva context
    SYNONYM_MAP = {
        "sync": ["synchronization", "syncing", "integration"],
        "presentation": ["deck", "slides", "CLM presentation"],
        "email": ["approved email", "AE", "RTE", "mass email", "HQ email"],
        "material": ["MLR", "asset", "document", "promotional material"],
        "content": ["asset", "material", "document"],
        "vault": ["Veeva Vault", "PromoMats"],
        "error": ["issue", "problem", "bug", "failure"],
        "config": ["configuration", "settings", "setup"],
    }

    def optimize_query(self, question: str, thread_context: str = None) -> list:
        """Optimize a query for better vector search results.

        Args:
            question: Original user question
            thread_context: Optional conversation context

        Returns:
            list of optimized query strings to search with
        """
        # Use Claude to generate optimized queries
        context_hint = ""
        if thread_context:
            context_hint = f"""
Previous conversation context:
{thread_context}

Use this to understand what the current question refers to.
"""

        prompt = f"""You are a search query optimizer for a knowledge base about Shaman (a pharma content platform) and Veeva integrations.

TASK: Generate 3 optimized search queries for the user's question.

RULES:
1. Expand acronyms:
   - CLM = Closed Loop Marketing (presentations for sales reps)
   - MLR = Material in Veeva, promotional/non-promotional marketing and sales material
   - HCP = Healthcare Professional
   - AE = Approved Email = RTE (Rep Triggered Email)
   - RTE = Rep Triggered Email = Approved Email
   - ME = Mass Email = HQ Email = Marketing Email
   - DAM = Digital Asset Management

2. Add relevant context words for pharma/Veeva domain

3. If it's a follow-up question, incorporate context from previous messages

4. Remove filler words ("hi", "please", "can you", "I want to")

5. Generate 3 different query variations:
   - Query 1: Direct expansion of the question
   - Query 2: Add technical/domain terms
   - Query 3: Focus on potential solutions/how-to
{context_hint}
USER QUESTION: {question}

Respond with exactly 3 queries, one per line, no numbering or bullets. Just the queries."""

        try:
            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            queries = response.content[0].text.strip().split('\n')
            queries = [q.strip() for q in queries if q.strip()]

            # Always include the original question too
            if question not in queries:
                queries.insert(0, question)

            return queries[:4]  # Max 4 queries

        except Exception as e:
            # Fallback: simple expansion
            return self._simple_query_expansion(question)

    def _simple_query_expansion(self, question: str) -> list:
        """Fallback query expansion without LLM."""
        queries = [question]

        # Expand acronyms (loaded from terminology.json)
        expanded = question
        acronym_map = self.load_acronyms()
        for acronym, expansion in acronym_map.items():
            if acronym in question.upper():
                expanded = expanded.replace(acronym, expansion)
                expanded = expanded.replace(acronym.lower(), expansion)

        if expanded != question:
            queries.append(expanded)

        # Add synonym variations
        for word, synonyms in self.SYNONYM_MAP.items():
            if word in question.lower():
                queries.append(question.lower().replace(word, synonyms[0]))

        return queries[:3]

    # =========================================================================
    # INTENT CLASSIFIER
    # =========================================================================

    def classify_intent(self, question: str, thread_context: str = None) -> dict:
        """Classify the intent of a question, detect ambiguity, and extract entities.

        Args:
            question: The user's question
            thread_context: Optional previous messages in the thread for context

        Returns:
            dict with keys:
                - intent: one of the intents below
                - confidence: float 0-1
                - reason: brief explanation
                - is_ambiguous: bool - whether the question needs clarification
                - clarifying_questions: list of questions to ask if ambiguous
                - entities: dict with extracted entities (customer, error_code, feature, urgency)
        """
        context_section = ""
        if thread_context:
            context_section = f"""
THREAD CONTEXT (previous messages in this conversation):
{thread_context}

Use this context to understand follow-up questions like "what about X?" or "and for customer Y?"
"""

        prompt = """Analyze this message from an internal Shaman support team member.

DOMAIN CONTEXT (Shaman platform):
- "Sync" issues are common - relate to Shaman↔Veeva synchronization
- Account names follow pattern: Company + Region + Product Area (e.g., "Novartis UK IMM", "GSK Brazil")
- "Vault" = Veeva Vault document management system
- "CLM" = Closed Loop Marketing presentations
- "AE" = Approved Email templates
- "MLR" = Medical, Legal, Regulatory review process
- French customers may ask in French - treat same as English intent

TASK 1 - CLASSIFY INTENT (choose the most specific one):
- how_to: Questions about how to do something, feature inquiries, "does anyone know...", "Is it possible to..."
- sync_issue: Synchronization problems between Shaman and Veeva ("sync hours", "resync", "not syncing")
- template_issue: Email/presentation template problems, token rendering, custom tokens not displaying
- bug_veeva: Bug related to Veeva integration (vault tokens, CLM, approved email - NOT sync timing issues)
- bug_config: Bug related to configuration (missing values, settings not applied, field mappings, policy issues)
- bug_product: Bug in product functionality (UI issues, buttons inactive, errors, PDF generation problems)
- feature_request: Suggesting new functionality or improvements
- escalation: Urgent issue, customer escalation, multiple follow-ups, needs immediate human attention
- greeting: ONLY pure greetings with no topic: "hi", "hello", "thanks", "bye". NOT queries like "Shaman team" or "support contacts"

HIGH CONFIDENCE PATTERNS (0.9+):
- "does anyone know" → how_to (0.95)
- "Is it possible to..." / "Can we..." → how_to (0.9)
- "what does [feature] do" / "what is [feature]" → how_to (0.95)
- Queries about teams/people/organization/contacts → how_to (0.95)
- Short topic phrases like "Shaman support team" → how_to (0.9) - these are INFORMATION REQUESTS, not greetings
- "Can you please take a look" + account context → escalation (0.9)
- "Should be fixed, can user try to resync?" → bug_product (0.95)
- "sync" + "hours/time" → sync_issue (0.9)
- "Message Tags are being synced" → sync_issue (0.9)
- "Create button remains inactive" → bug_product (0.95)
- "error when trying to download" → bug_product (0.9)
- "tokens are not rendering" / "custom tokens" → template_issue (0.9)
- "presentations have not been created in Vault" → bug_veeva (0.9)
- Error messages in quotes or Sentry URLs → bug_product (0.9)
- "policy" + "not working" → bug_config (0.9)

AMBIGUOUS PATTERNS (need clarification):
- "Do you have any news?" → too vague, could be follow-up on anything
- "Can you help me?" → too generic
- "Is it the expected behaviour" → unclear if bug or how-to
- "What am I missing?" → could be config or user error

URGENCY SIGNALS (→ escalation or high urgency):
- Multiple @ mentions in message
- "OOO" (out of office) mentioned
- Intercom/Zendesk conversation links included
- "sorry to chase you", "urgent", "before Monday"
- Multiple follow-ups on same issue

TASK 2 - DETECT AMBIGUITY:
Mark as ambiguous if:
- The question is too vague to search effectively (e.g., "it doesn't work", "help me")
- Missing critical context like: which customer/account, which feature, what error message
- Could apply to multiple unrelated features or scenarios

DO NOT mark as ambiguous if:
- It's a greeting
- It's a feature inquiry ("what does X do")
- It mentions specific features, errors, accounts, or context
- It's a clear sync/template/bug issue with details

TASK 3 - EXTRACT ENTITIES:
Extract any mentioned:
- customer: Company/account name - pattern: "Company + Region + Dept" (e.g., "Novartis UK IMM", "Galderma Alpine Aesthetics")
- error_code: Error messages in quotes, Sentry URLs, HTTP status codes, technical error class names
- feature: CamelCase names (SetToStageCLM), hyphenated terms (Message Tags), or: Clickstream, Smart Update, Visual Builder, CLM, AE
- urgency: low/medium/high/critical based on signals above
""" + context_section + """
MESSAGE: {question}

Respond in this exact JSON format:
{{
  "intent": "<intent>",
  "confidence": <0.0-1.0>,
  "reason": "<brief explanation>",
  "is_ambiguous": <true/false>,
  "clarifying_questions": ["<question 1>", "<question 2>"],
  "entities": {{
    "customer": "<customer name or null>",
    "error_code": "<error code or null>",
    "feature": "<feature name or null>",
    "urgency": "<low/medium/high/critical>"
  }}
}}

Rules:
- clarifying_questions: Only include if is_ambiguous is true (1-3 short questions)
- entities: Use null for fields not mentioned, default urgency to "low"

Only output the JSON, nothing else."""

        try:
            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt.format(question=question)}]
            )

            import json
            result = json.loads(response.content[0].text.strip())
            # Ensure all expected keys exist
            result.setdefault("is_ambiguous", False)
            result.setdefault("clarifying_questions", [])
            result.setdefault("entities", {"customer": None, "error_code": None, "feature": None, "urgency": "low"})
            return result
        except Exception as e:
            # Default to how_to if classification fails
            return {
                "intent": "how_to",
                "confidence": 0.5,
                "reason": f"Classification failed: {e}",
                "is_ambiguous": False,
                "clarifying_questions": [],
                "entities": {"customer": None, "error_code": None, "feature": None, "urgency": "low"}
            }

    def get_intent_response(self, intent: str, question: str, entities: dict = None) -> str:
        """Get a response prefix based on intent, including extracted entities."""
        # Build entities block if we have any
        entity_block = ""
        if entities:
            entity_lines = []
            if entities.get("customer"):
                entity_lines.append(f"**Customer:** {entities['customer']}")
            if entities.get("error_code"):
                entity_lines.append(f"**Error:** {entities['error_code']}")
            if entities.get("feature"):
                entity_lines.append(f"**Feature:** {entities['feature']}")
            if entities.get("urgency") and entities["urgency"] != "low":
                urgency_emoji = {"medium": ":large_yellow_circle:", "high": ":large_orange_circle:", "critical": ":red_circle:"}.get(entities["urgency"], "")
                entity_lines.append(f"**Urgency:** {urgency_emoji} {entities['urgency'].upper()}")
            if entity_lines:
                entity_block = "\n".join(entity_lines) + "\n\n"

        if intent == "bug_veeva":
            return (
                "**:bug: Veeva Integration Issue**\n\n"
                f"{entity_block}"
                "Create a ticket on the **OPS board** for ConfigOps with:\n"
                "• Customer/account name\n"
                "• Veeva Vault environment\n"
                "• Steps to reproduce\n"
                "• Error messages (if any)\n\n"
                "Here's what I found in the knowledge base:\n\n"
            )
        elif intent == "bug_config":
            return (
                "**:gear: Configuration Issue**\n\n"
                f"{entity_block}"
                "This looks like a configuration issue. Create a ticket on the **OPS board** for ConfigOps with:\n"
                "• Customer/account name\n"
                "• What setting/value is affected\n"
                "• Expected vs actual behavior\n\n"
                "Here's related information:\n\n"
            )
        elif intent == "bug_product":
            return (
                "**:bug: Product Bug**\n\n"
                f"{entity_block}"
                "Please report this in the **#qa-hero** channel with:\n"
                "• Steps to reproduce\n"
                "• Expected vs actual behavior\n"
                "• Screenshots/recordings if possible\n\n"
                "Here's what I found that might help:\n\n"
            )
        elif intent == "sync_issue":
            return (
                "**:arrows_counterclockwise: Sync Issue**\n\n"
                f"{entity_block}"
                "This appears to be a Shaman↔Veeva synchronization issue.\n\n"
                "Common checks:\n"
                "• Verify sync schedule in Superadmin\n"
                "• Check Veeva Vault connection status\n"
                "• Review sync logs for errors\n\n"
                "Here's what I found:\n\n"
            )
        elif intent == "template_issue":
            return (
                "**:page_facing_up: Template Issue**\n\n"
                f"{entity_block}"
                "This appears to be a template or token rendering issue.\n\n"
                "Common causes:\n"
                "• Token syntax: `{{customText[...]}}`\n"
                "• Missing token values in account config\n"
                "• Template not published to correct stage\n\n"
                "Here's what I found:\n\n"
            )
        elif intent == "feature_request":
            return (
                "**:bulb: Feature Request**\n\n"
                f"{entity_block}"
                "First, let me check if this feature already exists...\n\n"
                "If not available, please share in the **#feature-requests** channel.\n\n"
            )
        elif intent == "escalation":
            return (
                "**:warning: This seems urgent!** <@product-team>\n\n"
                f"{entity_block}"
                "I've flagged this for human attention. While waiting for the team:\n\n"
            )
        elif intent == "greeting":
            return None  # Will return a simple greeting instead
        else:  # how_to
            if entity_block:
                return entity_block
            return ""  # No prefix, just answer

    # =========================================================================
    # SEARCH & Q&A
    # =========================================================================

    def search(self, query: str, n_results: int = 10, sources: list = None, optimize: bool = False, thread_context: str = None, customer_key: str = None) -> list:
        """Search across all or selected sources.

        Args:
            query: Search query
            n_results: Number of results to return
            sources: List of sources to search (None = all)
            optimize: Whether to use query optimization for better recall
            thread_context: Conversation context for query optimization
            customer_key: Optional customer key to include customer-specific docs
        """
        if sources is None:
            sources = ["slack", "helpcenter", "intercom", "veeva", "pdf", "manual", "confluence", "video", "features", "community"]

        # Get queries to search with
        if optimize:
            queries = self.optimize_query(query, thread_context)
        else:
            queries = [query]

        all_results = []
        seen_ids = set()  # For deduplication
        per_source = max(3, n_results // len(sources))

        for search_query in queries:
            for source in sources:
                if source in self.collections and self.collections[source].count() > 0:
                    # Fetch more results from Confluence (pages split into many chunks)
                    source_limit = per_source * 3 if source == "confluence" else per_source
                    results = self.collections[source].query(
                        query_texts=[search_query],
                        n_results=source_limit
                    )

                    docs = results.get("documents", [[]])[0]
                    metas = results.get("metadatas", [[]])[0]
                    dists = results.get("distances", [[]])[0]
                    ids = results.get("ids", [[]])[0]

                    for doc, meta, dist, doc_id in zip(docs, metas, dists, ids):
                        # Deduplicate by document ID
                        if doc_id in seen_ids:
                            continue
                        seen_ids.add(doc_id)

                        # Calculate base relevance
                        relevance = 1 - dist

                        # Boost Confluence overall (authoritative documentation)
                        if source == "confluence":
                            relevance += 0.08  # General boost for official docs

                            # Extra boost for organizational queries (team, who, organization, etc.)
                            org_keywords = ["team", "who is", "who are", "organization", "member", "role", "lead", "manager"]
                            if any(kw in query.lower() for kw in org_keywords):
                                relevance += 0.12  # Additional boost for org questions

                        all_results.append({
                            "content": doc,
                            "metadata": meta,
                            "relevance": relevance,
                            "source": source
                        })

            # Search customer-specific collection if provided
            if customer_key:
                try:
                    customer_collection = self.get_customer_collection(customer_key)
                    if customer_collection.count() > 0:
                        results = customer_collection.query(
                            query_texts=[search_query],
                            n_results=per_source
                        )

                        docs = results.get("documents", [[]])[0]
                        metas = results.get("metadatas", [[]])[0]
                        dists = results.get("distances", [[]])[0]
                        ids = results.get("ids", [[]])[0]

                        for doc, meta, dist, doc_id in zip(docs, metas, dists, ids):
                            if doc_id in seen_ids:
                                continue
                            seen_ids.add(doc_id)

                            # High boost for customer-specific docs to ensure they appear in results
                            # Customer docs are curated and should be prioritized when in customer channel
                            all_results.append({
                                "content": doc,
                                "metadata": meta,
                                "relevance": (1 - dist) + 0.5,  # Add 0.5 to ensure customer docs rank high
                                "source": f"customer_{customer_key}"
                            })
                except Exception:
                    pass  # Customer collection doesn't exist yet

        # Sort by relevance
        all_results.sort(key=lambda x: x["relevance"], reverse=True)
        return all_results[:n_results]

    def ask(self, question: str, n_context: int = 15, sources: list = None, classify: bool = True, thread_context: str = None, customer_key: str = None) -> tuple:
        """Ask a question using RAG across all sources.

        Args:
            question: The question to ask
            n_context: Number of context results to use
            sources: List of sources to search (None = all)
            classify: Whether to classify intent first
            thread_context: Previous messages in thread for context (follow-up questions)
            customer_key: Optional customer key to include customer-specific docs

        Returns:
            tuple: (answer, intent_info) where intent_info is dict or None
        """
        intent_info = None
        low_confidence_warning = ""

        # Classify intent if enabled
        if classify:
            intent_info = self.classify_intent(question, thread_context=thread_context)

            # Handle greetings directly
            if intent_info.get("intent") == "greeting":
                return ("Hi! I'm MagicAnswer. Ask me anything about Shaman, Veeva, or check the knowledge base. "
                        "Type `stats` to see what's indexed.", intent_info)

            # Handle ambiguous questions - ask for clarification
            if intent_info.get("is_ambiguous") and intent_info.get("clarifying_questions"):
                questions = intent_info["clarifying_questions"]
                clarification_msg = "**:thinking_face: I need a bit more context to help you better.**\n\n"
                for q in questions:
                    clarification_msg += f"• {q}\n"
                clarification_msg += "\nPlease provide more details and I'll search the knowledge base."
                return (clarification_msg, intent_info)

            # Low confidence - search anyway but add a note
            # Only show warning for very low confidence (< 0.5) since we search anyway
            # Questions with specific features/context should not get warnings
            if intent_info.get("confidence", 1.0) < 0.5:
                low_confidence_warning = "_Note: I'm not entirely sure about the intent of your question. Here's what I found:_\n\n"

        # Search with query optimization enabled for better recall
        results = self.search(question, n_results=n_context, sources=sources, optimize=True, thread_context=thread_context, customer_key=customer_key)

        # Check for customer-specific Slack sources used outside their channel
        # Only warn for Veeva/integration topics which are truly customer-specific
        customer_source_warning = ""
        other_customer_sources = set()

        # Keywords that indicate customer-specific topics (Veeva workflows, integrations)
        customer_specific_keywords = [
            "veeva", "vault", "vvpm", "promomats", "workflow", "integration",
            "sync", "mlr", "lifecycle", "staging", "crm"
        ]
        question_lower = question.lower()
        is_customer_specific_topic = any(kw in question_lower for kw in customer_specific_keywords)

        if is_customer_specific_topic:
            for r in results:
                if r.get("source") == "slack":
                    channel_name = r.get("metadata", {}).get("channel", "")
                    source_customer = get_customer_by_channel_name(channel_name)
                    if source_customer and source_customer != customer_key:
                        other_customer_sources.add(source_customer)

            if other_customer_sources:
                customer_names = [CUSTOMERS.get(c, {}).get("name", c) for c in other_customer_sources]
                customer_source_warning = (
                    f"⚠️ _Note: Some information below comes from **{', '.join(customer_names)}** channel(s). "
                    f"Veeva workflows, integrations, and configurations may differ per customer._\n\n"
                )

        if not results:
            return ("I couldn't find relevant information in the knowledge base. "
                    "Try rephrasing your question or check if the topic has been indexed.", intent_info)

        # Build context grouped by source
        context_parts = []
        for i, r in enumerate(results, 1):
            # Handle customer-specific sources
            source = r["source"]
            if source.startswith("customer_"):
                cust_key = source.replace("customer_", "")
                cust_config = CUSTOMERS.get(cust_key, {})
                source_label = f"🏢 {cust_config.get('name', cust_key)} Docs"
            else:
                source_label = {
                    "slack": "💬 Slack",
                    "helpcenter": "📚 Help Center",
                    "intercom": "🎫 Intercom",
                    "veeva": "📗 Veeva Docs",
                    "pdf": "📄 PDF Document",
                    "manual": "📝 Manual Doc",
                    "confluence": "📘 Confluence",
                    "video": "🎬 Video",
                    "features": "📋 Features",
                    "community": "👥 Community"
                }.get(source, source)

            context_parts.append(f"[{i}] {source_label}\n{r['content'][:1500]}")

        context = "\n\n---\n\n".join(context_parts)

        # Build conversation history section if available
        conversation_history = ""
        if thread_context:
            conversation_history = f"""
CONVERSATION HISTORY (previous messages in this thread):
{thread_context}

Use this history to understand follow-up questions. For example, if the user previously asked about "Veeva sync" and now asks "what about CLM?", they mean "what about CLM sync to Veeva?"
"""

        prompt = f"""You are MagicAnswer, an internal assistant for Shaman staff (Support, Customer Success, Product teams).

SHAMAN PLATFORM OVERVIEW:
Shaman is a content authoring platform for the pharmaceutical industry, designed to create, localize, manage, and update approved multichannel marketing and medical content.

Shaman is a GUIDED SELF-SERVICE AUTHORING PLATFORM for end users such as brand marketers and digital managers (not agency designers). This makes local pharma teams more agile, flexible, faster and saves budget.

Guided self-service means:
- Author content in self-service capability
- Structured, simplified process between reusable components and Veeva PromoMats for review/approval
- Automates steps by integrating with Veeva, respecting MLR status from Veeva in Shaman
- Pre-authoring guardrails: design templates, locked elements
- Authoring guardrails: brand design system, limited colors/fonts, content cards, references
- Post-authoring: compliance assistants check quality, language, MLR compare

SHAMAN BUILDERS (Authoring Capabilities):
| Subdomain | Builder | Purpose |
| AE | Approved Email Builder | Veeva Approved Emails for compliant HCP communication |
| ME | Marketing Email Builder | Non-MLR/pre-MLR emails, typically exported to SFMC (also "Mass Email" or "HQ Email") |
| CLM | CLM Builder | Assemble interactive HTML CLM presentations for Veeva CRM |
| SC | Slide Builder | Create/edit slides from templates or imported PDF, used in CLM Builder |
| VA | Visual Asset Builder | Images, banners, graphics, print PDFs, exported to visual library |
| WEB | Landing Page Builder | HTML landing pages, similar to email builders |
| CC | Smart Content Cards | Pre-approved modular components (text, images, references) for use in other builders |

BUILDER TECHNICAL NOTES:
- MA, AE and Web share the same HTML editor
- Slide and Visual share the same canvas editor
- CLM uses an assembler (not editor)
- Builders have different tabs/functionality
- Email builders are most feature-mature

ACRONYM REFERENCE:
- AE = Approved Email = RTE (Rep Triggered Email)
- ME = Mass Email = HQ Email = Marketing Email
- CLM = Closed Loop Marketing (sales presentations)
- MLR = Material (in Veeva context - promotional/non-promotional content requiring review)
- CC = Smart Content Cards (modular content components)
- SFMC = Salesforce Marketing Cloud

INTERNAL CONTEXT:
- ConfigOps = internal administrators who handle configuration changes
- Superadmin = Shaman's backend admin system for ConfigOps and Product
- OPS board = Jira board for ConfigOps tickets
- When something requires admin/backend changes, guide users to create a ticket for ConfigOps on the OPS board

ACCOUNT MODEL:
- Each account is logically isolated (encrypted RDS, encrypted S3, no cross-account leakage)
- Accounts created per country (eg: Almirall-ES) or per country+therapeutic area (eg: Takeda-DE-Onco)
- Account groups allow shared content, templates, settings while preserving boundaries
- Users maintained in userpool per account group (AWS Cognito with SSO support)

FEATURE CONFIGURATION MODEL:
- Shaman has a single code base - all functionality is feature-flag driven
- Features configured per account, often scoped per builder
- Account-level features apply to the ENTIRE account, not per content type
- You CANNOT have different feature settings for AE vs ME on same account
- If customer needs different behavior, they need separate accounts or product enhancement

KEY REASONING RULES:
1. Not all builders are equal - ME ≠ AE ≠ CLM (different purposes, governance, maturity)
2. MLR review only applies where enabled - usually AE, ME and CLM or slides
3. Export targets differ: AE/CLM → Veeva, ME → SFMC, WEB/VA/CC → ZIP/HTML/PDF
4. Content is modular, not flat (CCs reused across builders)
5. Approval ≠ creation (different workflows)
6. Feature availability is account-specific and often builder-scoped
{conversation_history}
SOURCE PRIORITY:
- For team structure, organization, roles, or "who is in team X" questions: PREFER Confluence sources (official documentation) over Slack/Intercom conversations
- Slack/Intercom conversations may show people doing tasks for OTHER teams (e.g., someone helping with support doesn't mean they're ON the support team)
- Confluence organizational docs are authoritative for current team membership

STRICT RULES:
1. ONLY use information from the KNOWLEDGE BASE CONTEXT below - never make up or assume information
2. If the answer is not in the context, say "I couldn't find this information in the knowledge base"
3. NEVER share passwords, API keys, tokens, or any credentials
4. Keep answers concise and actionable
5. For follow-up questions, connect them to the previous conversation topic
6. When discussing features, clarify they are account-level settings that apply to the entire account

RESPONSE FORMAT:
Start with "**Summary:**" followed by a brief 1-2 sentence answer in simple terms.
If something requires configuration changes, mention that ConfigOps can handle it via an OPS board ticket.
Then provide additional details only if necessary.

FORMATTING RULES:
- Use backticks for feature names: `SetToStageCLM`, `ShamanGlobalExport`
- Use backticks for Superadmin values, settings, and configuration keys
- Use backticks for builder codes: `AE`, `ME`, `CLM`

KNOWLEDGE BASE CONTEXT:
{context}

CURRENT QUESTION: {question}

Remember: Be concise. Start with a summary. Connect follow-up questions to previous topics."""

        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        answer = response.content[0].text

        # Prepend intent-specific response if applicable
        if intent_info:
            entities = intent_info.get("entities")
            if intent_info.get("intent") != "how_to" or entities:
                prefix = self.get_intent_response(intent_info["intent"], question, entities)
                if prefix:
                    answer = prefix + answer

        # Add low confidence warning if applicable
        if low_confidence_warning:
            answer = low_confidence_warning + answer

        # Add customer source warning if applicable
        if customer_source_warning:
            answer = customer_source_warning + answer

        return (answer, intent_info)

    def interactive_mode(self):
        """Run interactive Q&A mode."""
        print("\n" + "=" * 60)
        print("MULTI-SOURCE RAG - INTERACTIVE MODE")
        print("=" * 60)

        # Show stats
        print("\n📊 Knowledge base:")
        print(f"   💬 Slack:       {self.collections['slack'].count()} messages")
        print(f"   📚 Help Center: {self.collections['helpcenter'].count()} articles")
        print(f"   🎫 Intercom:    {self.collections['intercom'].count()} conversations")
        print(f"   📗 Veeva:       {self.collections['veeva'].count()} docs")
        print(f"   📄 PDFs:        {self.collections['pdf'].count()} chunks")
        print(f"   📝 Manual:      {self.collections['manual'].count()} chunks")
        print(f"   📘 Confluence:  {self.collections['confluence'].count()} pages")
        # Show customer stats
        customer_stats = self.get_customer_stats()
        if any(v > 0 for v in customer_stats.values()):
            print("   🏢 Customers:")
            for cust_key, count in customer_stats.items():
                if count > 0:
                    print(f"      - {cust_key}: {count} items")

        total = sum(c.count() for c in self.collections.values())
        if total == 0:
            print("\n⚠ Knowledge base is empty. Run with --sync-all first.")
            return

        print("\nCommands:")
        print("  <question>                    Ask across all sources")
        print("  /slack <question>             Search only Slack")
        print("  /help <question>              Search only Help Center")
        print("  /tickets <question>           Search only Intercom")
        print("  /veeva <question>             Search only indexed Veeva docs")
        print("  /pdf <question>               Search only PDF documents")
        print("  /confluence <question>        Search only Confluence pages")
        print("  /veeva-live <question>        Search Veeva help center live (web)")
        print("  /search <query>               Show raw search results")
        print("  /stats                        Show statistics")
        print("  /quit                         Exit")
        print("-" * 60)

        while True:
            try:
                user_input = input("\n❓ ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            if user_input.lower() == "/stats":
                print(f"\n📊 Knowledge base:")
                print(f"   💬 Slack:       {self.collections['slack'].count()} messages")
                print(f"   📚 Help Center: {self.collections['helpcenter'].count()} articles")
                print(f"   🎫 Intercom:    {self.collections['intercom'].count()} conversations")
                print(f"   📗 Veeva:       {self.collections['veeva'].count()} docs")
                print(f"   📄 PDFs:        {self.collections['pdf'].count()} chunks")
                print(f"   📝 Manual:      {self.collections['manual'].count()} chunks")
                print(f"   📘 Confluence:  {self.collections['confluence'].count()} pages")
                # Show customer stats
                customer_stats = self.get_customer_stats()
                if any(v > 0 for v in customer_stats.values()):
                    print("   🏢 Customers:")
                    for cust_key, count in customer_stats.items():
                        if count > 0:
                            print(f"      - {cust_key}: {count} items")
                continue

            if user_input.lower().startswith("/search "):
                query = user_input[8:].strip()
                results = self.search(query, n_results=5)
                for i, r in enumerate(results, 1):
                    print(f"\n[{i}] {r['source']} (relevance: {r['relevance']:.2f})")
                    print(f"    {r['content'][:200]}...")
                continue

            # Live Veeva search (web fallback)
            if user_input.lower().startswith("/veeva-live "):
                question = user_input[12:].strip()
                print("\n🌐 Searching Veeva live...")
                try:
                    answer = self.veeva_live_search(question)
                    print(f"\n💬 {answer}")
                except Exception as e:
                    print(f"\n⚠ Error: {e}")
                continue

            # Source-specific searches
            sources = None
            question = user_input

            if user_input.lower().startswith("/slack "):
                sources = ["slack"]
                question = user_input[7:].strip()
            elif user_input.lower().startswith("/help "):
                sources = ["helpcenter"]
                question = user_input[6:].strip()
            elif user_input.lower().startswith("/tickets "):
                sources = ["intercom"]
                question = user_input[9:].strip()
            elif user_input.lower().startswith("/veeva "):
                sources = ["veeva"]
                question = user_input[7:].strip()
            elif user_input.lower().startswith("/pdf "):
                sources = ["pdf"]
                question = user_input[5:].strip()
            elif user_input.lower().startswith("/confluence "):
                sources = ["confluence"]
                question = user_input[12:].strip()

            print("\n🤔 Thinking...")
            try:
                answer, intent_info = self.ask(question, sources=sources)
                if intent_info:
                    intent = intent_info.get("intent", "unknown")
                    confidence = intent_info.get("confidence", 0)
                    print(f"\n🏷️  Intent: {intent} ({confidence:.0%})")
                print(f"\n💬 {answer}")
            except Exception as e:
                print(f"\n⚠ Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Multi-Source RAG System")
    parser.add_argument("--sync-all", action="store_true", help="Sync all sources")
    parser.add_argument("--sync-slack", action="store_true", help="Sync only Slack")
    parser.add_argument("--channels", type=str, help="Comma-separated Slack channels (use with --sync-slack or --sync-all)")
    parser.add_argument("--sync-helpcenter", action="store_true", help="Sync only Help Center")
    parser.add_argument("--sync-intercom", action="store_true", help="Sync only Intercom conversations")
    parser.add_argument("--sync-veeva", action="store_true", help="Sync only Veeva Help Center")
    parser.add_argument("--sync-pdfs", action="store_true", help="Sync PDF documents from content_input/pdfs/")
    parser.add_argument("--vision", action="store_true", help="Use Claude Vision to analyze images in PDFs (use with --sync-pdfs)")
    parser.add_argument("--pdf-folder", type=str, metavar="PATH", help="Custom PDF folder path (use with --sync-pdfs)")
    parser.add_argument("--sync-manual", action="store_true", help="Sync manual documents from content_input/manual_docs/")
    parser.add_argument("--sync-confluence", action="store_true", help="Sync Confluence pages")
    parser.add_argument("--sync-context", action="store_true", help="Sync Shaman platform context document")
    parser.add_argument("--sync-terminology", action="store_true", help="Sync terminology glossary from content_input/terminology.json")
    parser.add_argument("--reindex-features", type=str, metavar="JSON_FILE", help="Force re-index features from JSON file (clears existing)")
    parser.add_argument("--sync-customer", type=str, nargs="?", const="all", metavar="CUSTOMER", help="Sync customer docs (specify customer key or 'all')")
    parser.add_argument("--export", type=str, metavar="SOURCE", help="Export collection to JSON (slack, intercom, helpcenter, veeva, pdf, manual, confluence)")
    parser.add_argument("--import", type=str, dest="import_source", metavar="SOURCE", help="Import collection from JSON backup")
    parser.add_argument("--export-customers", action="store_true", help="Export all customer data to customer_data_backup.json")
    parser.add_argument("--import-customers", action="store_true", help="Import customer data from customer_data_backup.json")
    parser.add_argument("--restore-all", action="store_true", help="Restore all sources from backups and re-sync manual/context/features")
    parser.add_argument("--ask", type=str, help="Ask a single question")
    parser.add_argument("--debug", action="store_true", help="Show retrieved documents (use with --ask)")
    parser.add_argument("--export-context", type=str, metavar="TOPIC", help="Export knowledge base context on a topic to a text file for use with external LLMs")
    args = parser.parse_args()

    rag = MultiSourceRAG()

    # Parse channels if provided
    channels = None
    if args.channels:
        channels = [c.strip() for c in args.channels.split(",") if c.strip()]

    if args.restore_all:
        # Restore all sources from backups
        print("\n" + "=" * 60)
        print("🔄 RESTORING ALL SOURCES FROM BACKUPS")
        print("=" * 60)

        # Check for backups folder first, then root
        backup_dir = "backups" if os.path.isdir("backups") else "."

        # Import from backups
        backup_sources = ["slack", "intercom", "helpcenter", "confluence", "veeva", "pdf", "community"]
        for source in backup_sources:
            backup_file = os.path.join(backup_dir, f"{source}_backup.json")
            if os.path.exists(backup_file):
                print(f"\n📥 Importing {source} from {backup_file}...")
                try:
                    rag.import_collection(source, input_path=backup_file)
                except Exception as e:
                    print(f"   ⚠ Error importing {source}: {e}")
            else:
                print(f"\n⏭️  Skipping {source} (no backup file)")

        # Re-sync sources that don't have backups
        print("\n📄 Syncing manual documents...")
        rag.sync_manual()

        print("\n📋 Syncing context...")
        rag.sync_context()

        # Re-index features if file exists (check backups folder first)
        features_file = os.path.join(backup_dir, "superadmin_feature_register_enriched.json")
        if not os.path.exists(features_file):
            features_file = "superadmin_feature_register_enriched.json"
        if os.path.exists(features_file):
            print(f"\n🔧 Re-indexing features from {features_file}...")
            rag.sync_features(features_file, force=True)
        else:
            print(f"\n⏭️  Skipping features (no superadmin_feature_register_enriched.json)")

        # Re-index approved suggestions
        suggestions_file = "pending_suggestions.json"
        if os.path.exists(suggestions_file):
            try:
                with open(suggestions_file, 'r') as f:
                    suggestions = json.load(f)
                approved = [s for s in suggestions if s.get('status') == 'approved']
                if approved:
                    print(f"\n👥 Re-indexing {len(approved)} approved suggestions...")
                    for s in approved:
                        try:
                            rag.collections['community'].add(
                                documents=[s.get('enriched_text') or s.get('text')],
                                metadatas=[{'user': s.get('user_name'), 'date': s.get('timestamp'), 'type': 'suggestion'}],
                                ids=[f"suggestion_{s.get('id')}"]
                            )
                        except Exception as e:
                            print(f"   ⚠ Error re-indexing suggestion {s.get('id')}: {e}")
            except Exception as e:
                print(f"\n⏭️  Skipping suggestions: {e}")

        # Import customer data if backup exists
        customer_backup = os.path.join(backup_dir, "customer_data_backup.json")
        if os.path.exists(customer_backup):
            print(f"\n🏢 Importing customer data...")
            rag.import_customer_data(customer_backup)
        else:
            print(f"\n⏭️  Skipping customer data (no customer_data_backup.json)")

        print("\n" + "=" * 60)
        print("✅ RESTORE COMPLETE")
        print("=" * 60)

    elif args.sync_all:
        rag.sync_slack(channels)
        rag.sync_helpcenter()
        rag.sync_intercom_conversations()
        rag.sync_veeva()
        rag.sync_pdfs()
        rag.sync_manual()
        rag.sync_confluence()
        rag.sync_context()
    elif args.sync_slack:
        rag.sync_slack(channels)
    elif args.sync_helpcenter:
        rag.sync_helpcenter()
    elif args.sync_intercom:
        rag.sync_intercom_conversations()
    elif args.sync_veeva:
        rag.sync_veeva()
    elif args.sync_pdfs:
        rag.sync_pdfs(pdf_folder=args.pdf_folder, use_vision=args.vision)
    elif args.sync_manual:
        rag.sync_manual()
    elif args.sync_confluence:
        rag.sync_confluence()
    elif args.sync_context:
        rag.sync_context()
    elif args.sync_terminology:
        rag.sync_terminology()
    elif args.reindex_features:
        rag.sync_features(args.reindex_features, force=True)
    elif args.sync_customer:
        if args.sync_customer == "all":
            rag.sync_customer_docs()  # Sync all customers
        else:
            rag.sync_customer_docs(args.sync_customer)  # Sync specific customer
    elif args.export:
        rag.export_collection(args.export)
    elif args.import_source:
        rag.import_collection(args.import_source)
    elif args.export_customers:
        rag.export_customer_data()
    elif args.import_customers:
        rag.import_customer_data()
    elif args.ask:
        if args.debug:
            # Debug mode: show retrieved documents
            print("=" * 60)
            print("🔍 DEBUG: Retrieved Documents")
            print("=" * 60)
            results = rag.search(args.ask, n_results=15, optimize=True)
            for i, r in enumerate(results, 1):
                meta = r.get("metadata", {})
                print(f"\n--- Result {i} (relevance: {r['relevance']:.3f}, source: {r['source']}) ---")
                print(f"Title: {meta.get('title', meta.get('name', 'N/A'))}")
                print(f"URL: {meta.get('url', meta.get('link', 'N/A'))}")
                print(f"Content preview: {r['content'][:300]}...")
            print("\n" + "=" * 60)
            print("📝 ANSWER")
            print("=" * 60 + "\n")
        answer, intent_info = rag.ask(args.ask)
        if intent_info:
            print(f"[Intent: {intent_info.get('intent')}]\n")
        print(answer)
    elif args.export_context:
        # Export context on a topic for use with external LLMs
        topic = args.export_context
        print(f"🔍 Searching knowledge base for: {topic}")

        # Search with multiple query variations
        queries = [
            topic,
            f"{topic} how to",
            f"{topic} workflow process",
            f"{topic} configuration setup"
        ]

        all_results = []
        seen_content = set()

        for q in queries:
            results = rag.search(q, n_results=20, optimize=True)
            for r in results:
                # Deduplicate by content hash
                content_hash = hash(r['content'][:200])
                if content_hash not in seen_content:
                    seen_content.add(content_hash)
                    all_results.append(r)

        # Sort by relevance and take top 40
        all_results.sort(key=lambda x: x['relevance'], reverse=True)
        all_results = all_results[:40]

        # Generate output filename
        safe_topic = "".join(c if c.isalnum() else "_" for c in topic)[:30]
        output_file = f"context_export_{safe_topic}.txt"

        with open(output_file, 'w') as f:
            f.write(f"# Knowledge Base Context Export\n")
            f.write(f"# Topic: {topic}\n")
            f.write(f"# Results: {len(all_results)} chunks\n")
            f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M')}\n")
            f.write("=" * 80 + "\n\n")
            f.write("Use this context to help write documentation or answer questions about this topic.\n\n")
            f.write("=" * 80 + "\n\n")

            for i, r in enumerate(all_results, 1):
                meta = r.get("metadata", {})
                title = meta.get('title', meta.get('name', 'Untitled'))
                url = meta.get('url', meta.get('link', ''))
                source = r['source']

                f.write(f"--- CHUNK {i} (source: {source}, relevance: {r['relevance']:.2f}) ---\n")
                if title:
                    f.write(f"Title: {title}\n")
                if url:
                    f.write(f"URL: {url}\n")
                f.write(f"\n{r['content']}\n\n")

        print(f"✅ Exported {len(all_results)} chunks to: {output_file}")
        print(f"📋 Copy the contents and paste into Claude.ai or another LLM")
    else:
        rag.interactive_mode()


if __name__ == "__main__":
    main()