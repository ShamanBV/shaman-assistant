"""
Slack RAG System
================
Extract Slack messages into a searchable knowledge base and ask questions with Claude.

SETUP
-----
pip install slack-sdk anthropic python-dotenv chromadb sentence-transformers

USAGE
-----
1. First run: python slack_rag.py --sync
   This fetches messages and builds the vector database

2. Then: python slack_rag.py
   This starts interactive Q&A mode

3. Update data: python slack_rag.py --sync
   Re-run periodically to fetch new messages
"""

import os
import json
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic
import chromadb
from chromadb.utils import embedding_functions

# Load environment variables
load_dotenv()

# Configuration
CHANNELS_TO_INDEX = [
    "product-questions",
    "product-bugs",
    "qa-hero-cs-ops-boards",
    "customersuccess",
]  # Add more channels as needed
SLACK_DAYS_TO_FETCH = 365
INTERCOM_DAYS_TO_FETCH = 365
MAX_MESSAGES_PER_CHANNEL = 3000  # Max messages per channel
DB_PATH = "./slack_knowledge_base"  # Where to store the vector DB


class SlackRAG:
    def __init__(self):
        self.slack_client = self._init_slack()
        self.anthropic_client = self._init_anthropic()
        self.chroma_client = chromadb.PersistentClient(path=DB_PATH)

        # Use sentence-transformers for embeddings (runs locally)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        # Get or create collection
        self.collection = self.chroma_client.get_or_create_collection(
            name="slack_messages",
            embedding_function=self.embedding_fn,
            metadata={"description": "Slack messages knowledge base"}
        )

    def _init_slack(self) -> WebClient:
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise ValueError("SLACK_BOT_TOKEN not found in .env file")
        return WebClient(token=token)

    def _init_anthropic(self) -> anthropic.Anthropic:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in .env file")
        return anthropic.Anthropic(api_key=api_key)

    def _get_user_names(self, user_ids: set) -> dict:
        """Fetch display names for user IDs."""
        user_names = {}
        for user_id in user_ids:
            try:
                result = self.slack_client.users_info(user=user_id)
                profile = result["user"]["profile"]
                user_names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
            except SlackApiError:
                user_names[user_id] = user_id
        return user_names

    def _find_channel_id(self, channel_name: str) -> str:
        """Find channel ID by name."""
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

    def _fetch_messages(self, channel_id: str, channel_name: str, days: int, max_messages: int) -> list:
        """Fetch messages from a channel."""
        messages = []
        oldest = datetime.now() - timedelta(days=days)
        oldest_ts = oldest.timestamp()

        try:
            result = self.slack_client.conversations_history(
                channel=channel_id,
                oldest=str(oldest_ts),
                limit=min(max_messages, 200)
            )
            messages.extend(result["messages"])

            while result.get("has_more") and len(messages) < max_messages:
                result = self.slack_client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest_ts),
                    limit=min(max_messages - len(messages), 200),
                    cursor=result["response_metadata"]["next_cursor"]
                )
                messages.extend(result["messages"])

            return messages[:max_messages]
        except SlackApiError as e:
            print(f"Error fetching messages from {channel_name}: {e.response['error']}")
            return []

    def _fetch_thread_replies(self, channel_id: str, thread_ts: str) -> list:
        """Fetch replies in a thread."""
        try:
            result = self.slack_client.conversations_replies(channel=channel_id, ts=thread_ts)
            return result["messages"][1:]  # Exclude parent
        except SlackApiError:
            return []

    def sync_channel(self, channel_name: str):
        """Sync a channel's messages to the vector database."""
        print(f"\n📥 Syncing #{channel_name}...")

        # Find channel
        channel_id = self._find_channel_id(channel_name)
        print(f"   Found channel ID: {channel_id}")

        # Fetch messages
        messages = self._fetch_messages(channel_id, channel_name, DAYS_TO_FETCH, MAX_MESSAGES_PER_CHANNEL)
        print(f"   Fetched {len(messages)} messages")

        if not messages:
            return

        # Get user names
        user_ids = set()
        for msg in messages:
            if msg.get("user"):
                user_ids.add(msg["user"])

        # Fetch threads and collect more user IDs
        print("   Fetching thread replies...")
        threads_fetched = 0
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                replies = self._fetch_thread_replies(channel_id, msg["ts"])
                msg["_replies"] = replies
                threads_fetched += 1
                for reply in replies:
                    if reply.get("user"):
                        user_ids.add(reply["user"])

        print(f"   Fetched {threads_fetched} threads")

        # Get user names
        print("   Resolving user names...")
        user_names = self._get_user_names(user_ids)

        # Process and store messages
        print("   Indexing messages...")
        documents = []
        metadatas = []
        ids = []

        for msg in messages:
            if msg.get("subtype"):  # Skip system messages
                continue

            msg_id = f"{channel_name}_{msg['ts']}"

            # Skip if already indexed (by ID)
            existing = self.collection.get(ids=[msg_id])
            if existing and existing['ids']:
                continue

            user = user_names.get(msg.get("user", ""), "Unknown")
            text = msg.get("text", "")
            ts = datetime.fromtimestamp(float(msg["ts"]))

            # Replace user mentions
            for user_id, name in user_names.items():
                text = text.replace(f"<@{user_id}>", f"@{name}")

            # Build document with thread context
            doc_parts = [f"[{ts.strftime('%Y-%m-%d')}] {user}: {text}"]

            for reply in msg.get("_replies", []):
                reply_user = user_names.get(reply.get("user", ""), "Unknown")
                reply_text = reply.get("text", "")
                for user_id, name in user_names.items():
                    reply_text = reply_text.replace(f"<@{user_id}>", f"@{name}")
                doc_parts.append(f"  → {reply_user}: {reply_text}")

            full_doc = "\n".join(doc_parts)

            documents.append(full_doc)
            metadatas.append({
                "channel": channel_name,
                "user": user,
                "timestamp": msg["ts"],
                "date": ts.strftime('%Y-%m-%d'),
                "has_replies": len(msg.get("_replies", [])) > 0,
                "reply_count": len(msg.get("_replies", []))
            })
            ids.append(msg_id)

        # Add to collection in batches
        if documents:
            batch_size = 100
            for i in range(0, len(documents), batch_size):
                self.collection.add(
                    documents=documents[i:i + batch_size],
                    metadatas=metadatas[i:i + batch_size],
                    ids=ids[i:i + batch_size]
                )
            print(f"   ✓ Indexed {len(documents)} new messages")
        else:
            print("   ✓ No new messages to index")

    def sync_all_channels(self):
        """Sync all configured channels."""
        print("=" * 60)
        print("SLACK RAG - SYNC MODE")
        print("=" * 60)

        for channel in CHANNELS_TO_INDEX:
            try:
                self.sync_channel(channel)
            except Exception as e:
                print(f"   ⚠ Error syncing #{channel}: {e}")

        # Print stats
        total = self.collection.count()
        print(f"\n📊 Total messages in knowledge base: {total}")
        print("=" * 60)

    def search(self, query: str, n_results: int = 10) -> list:
        """Search the knowledge base."""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {
                "content": doc,
                "metadata": meta,
                "relevance": 1 - dist  # Convert distance to relevance score
            }
            for doc, meta, dist in zip(documents, metadatas, distances)
        ]

    def ask(self, question: str, n_context: int = 15) -> str:
        """Ask a question using RAG."""
        # Search for relevant context
        results = self.search(question, n_results=n_context)

        if not results:
            return "No relevant information found in the knowledge base. Try running --sync first."

        # Build context
        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(f"[{i}] {r['content']}")

        context = "\n\n".join(context_parts)

        # Ask Claude
        prompt = f"""You are a helpful assistant answering questions about a company's Slack conversations.
Use the following Slack message excerpts to answer the question. If the answer isn't in the context, say so.
Be specific and reference relevant messages when applicable.

CONTEXT FROM SLACK:
{context}

QUESTION: {question}

Provide a clear, helpful answer based on the Slack messages above."""

        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text

    def interactive_mode(self):
        """Run interactive Q&A mode."""
        print("=" * 60)
        print("SLACK RAG - INTERACTIVE MODE")
        print("=" * 60)

        total = self.collection.count()
        print(f"📚 Knowledge base contains {total} messages")
        print("\nCommands:")
        print("  Type your question to search and get AI answers")
        print("  /search <query>  - Show raw search results")
        print("  /stats           - Show database statistics")
        print("  /quit            - Exit")
        print("-" * 60)

        while True:
            try:
                user_input = input("\n❓ Your question: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            if user_input.lower() == "/stats":
                total = self.collection.count()
                print(f"\n📊 Total messages indexed: {total}")
                print(f"📁 Channels: {', '.join(CHANNELS_TO_INDEX)}")
                print(f"📅 Time range: Last {DAYS_TO_FETCH} days")
                continue

            if user_input.lower().startswith("/search "):
                query = user_input[8:].strip()
                print(f"\n🔍 Searching for: {query}\n")
                results = self.search(query, n_results=5)
                for i, r in enumerate(results, 1):
                    print(f"[{i}] (relevance: {r['relevance']:.2f})")
                    print(f"    Channel: #{r['metadata']['channel']}")
                    print(f"    Date: {r['metadata']['date']}")
                    print(f"    {r['content'][:200]}...")
                    print()
                continue

            # Regular question - use RAG
            print("\n🤔 Thinking...")
            try:
                answer = self.ask(user_input)
                print(f"\n💬 Answer:\n{answer}")
            except Exception as e:
                print(f"\n⚠ Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Slack RAG System")
    parser.add_argument("--sync", action="store_true", help="Sync Slack messages to knowledge base")
    parser.add_argument("--ask", type=str, help="Ask a single question (non-interactive)")
    args = parser.parse_args()

    rag = SlackRAG()

    if args.sync:
        rag.sync_all_channels()
    elif args.ask:
        print(rag.ask(args.ask))
    else:
        # Check if we have data
        if rag.collection.count() == 0:
            print("⚠ Knowledge base is empty. Run with --sync first to fetch Slack messages.")
            print("  Example: python slack_rag.py --sync")
            return
        rag.interactive_mode()


if __name__ == "__main__":
    main()