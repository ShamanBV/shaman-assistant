"""
MagicAnswer Slack Bot
=====================
Real-time Q&A bot that answers questions using the indexed knowledge base.

SETUP
-----
1. Create a Slack App at https://api.slack.com/apps
2. Enable Socket Mode (Settings → Socket Mode → Enable)
3. Create an App-Level Token with connections:write scope
4. Add Bot Token Scopes (OAuth & Permissions):
   - app_mentions:read
   - chat:write
   - im:history
   - im:read
   - im:write
5. Enable Events (Event Subscriptions → Subscribe to bot events):
   - app_mention
   - message.im
   - reaction_added (for suggestion approval)
6. Install the app to your workspace
7. Add to .env:
   SLACK_BOT_TOKEN=xoxb-your-bot-token
   SLACK_APP_TOKEN=xapp-your-app-token
   MAGICANSWER_ADMIN_CHANNEL=C0123456789  # Channel ID for suggestion approvals

USAGE
-----
python slack_bot.py

The bot will:
- Respond to @MagicAnswer mentions in channels
- Respond to direct messages
- Search all indexed sources and provide answers with Claude
"""

import os
import re
import json
import signal
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Import our RAG system
from multi_source_rag import MultiSourceRAG

# Import customer configuration
try:
    from customer_config import get_customer_by_channel, get_customer_config, get_all_customer_keys, CUSTOMERS
except ImportError:
    def get_customer_by_channel(channel_id): return None
    def get_customer_config(customer_key): return None
    def get_all_customer_keys(): return []
    CUSTOMERS = {}

# Load environment variables
load_dotenv()

# Feedback storage file
FEEDBACK_FILE = "feedback_log.json"

# Suggestions storage file
SUGGESTIONS_FILE = "pending_suggestions.json"

# Questions log file
QUESTIONS_LOG_FILE = "questions_log.json"

# Admin channel for suggestion approvals (set in .env as MAGICANSWER_ADMIN_CHANNEL)
ADMIN_CHANNEL = os.environ.get("MAGICANSWER_ADMIN_CHANNEL", "")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize the Slack app
app = App(token=os.environ.get("SLACK_MAGICANSWER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN"))

# Initialize RAG system (shared instance)
rag = None


def get_rag():
    """Get or initialize the RAG system."""
    global rag
    if rag is None:
        logger.info("Initializing RAG system...")
        rag = MultiSourceRAG()
        logger.info("RAG system ready")
    return rag


def log_question(
    question: str,
    user_id: str,
    user_name: str,
    channel: str,
    channel_name: str = None,
    is_follow_up: bool = False,
    is_feedback: bool = False,
    is_suggestion: bool = False,
    intent_info: dict = None,
    customer_key: str = None
):
    """Log each question to questions_log.json for analysis.

    Args:
        question: The question text
        user_id: Slack user ID
        user_name: Display name of the user
        channel: Channel ID
        channel_name: Channel name (if available)
        is_follow_up: True if this is a follow-up in a thread
        is_feedback: True if this is feedback (thumbs up/down)
        is_suggestion: True if this is a suggestion submission
        intent_info: Intent classification results (if available)
        customer_key: Customer key if from a customer-specific channel
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "question": question[:500],
        "user_id": user_id,
        "user_name": user_name,
        "channel": channel,
        "channel_name": channel_name,
        "customer": customer_key,
        "is_follow_up": is_follow_up,
        "is_feedback": is_feedback,
        "is_suggestion": is_suggestion,
        "intent": intent_info.get("intent") if intent_info else None,
        "confidence": intent_info.get("confidence") if intent_info else None,
        "entities": intent_info.get("entities") if intent_info else None
    }

    try:
        # Load existing log
        if os.path.exists(QUESTIONS_LOG_FILE):
            with open(QUESTIONS_LOG_FILE, 'r') as f:
                questions_log = json.load(f)
        else:
            questions_log = []

        questions_log.append(log_entry)

        # Save updated log
        with open(QUESTIONS_LOG_FILE, 'w') as f:
            json.dump(questions_log, f, indent=2)

        logger.info(f"Logged question from {user_name}: {question[:50]}...")
    except Exception as e:
        logger.error(f"Failed to log question: {e}")


def get_thread_context(client, channel: str, thread_ts: str, limit: int = 10) -> str:
    """Fetch previous messages in a thread for context."""
    try:
        if not thread_ts:
            return None

        result = client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=limit + 1  # +1 because it includes the current message
        )

        messages = result.get("messages", [])
        if len(messages) <= 1:
            return None  # No previous messages

        # Get bot user ID to identify bot messages
        auth_result = client.auth_test()
        bot_user_id = auth_result["user_id"]

        # Get previous messages (exclude the last one which is current)
        context_parts = []
        for msg in messages[:-1]:
            text = msg.get("text", "")
            if text:
                # Remove bot mentions
                text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()
                if text:
                    # Label who said what
                    if msg.get("user") == bot_user_id or msg.get("bot_id"):
                        context_parts.append(f"Assistant: {text[:500]}")
                    else:
                        context_parts.append(f"User: {text}")

        if context_parts:
            return "\n\n".join(context_parts[-limit:])
        return None
    except Exception as e:
        logger.warning(f"Could not fetch thread context: {e}")
        return None


def save_feedback(question: str, answer: str, feedback: str, user_id: str, intent_info: dict = None):
    """Save feedback to log file for learning."""
    feedback_entry = {
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "answer": answer[:500],  # Truncate for storage
        "feedback": feedback,  # "positive" or "negative"
        "user_id": user_id,
        "intent": intent_info.get("intent") if intent_info else None,
        "entities": intent_info.get("entities") if intent_info else None
    }

    try:
        # Load existing feedback
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE, 'r') as f:
                feedback_log = json.load(f)
        else:
            feedback_log = []

        feedback_log.append(feedback_entry)

        # Save updated feedback
        with open(FEEDBACK_FILE, 'w') as f:
            json.dump(feedback_log, f, indent=2)

        logger.info(f"Feedback saved: {feedback} for question: {question[:50]}...")
    except Exception as e:
        logger.error(f"Failed to save feedback: {e}")


def save_suggestion(suggestion_text: str, user_id: str, user_name: str, channel: str, ts: str) -> dict:
    """Save a suggestion to pending queue and return the suggestion object."""
    import uuid

    suggestion = {
        "id": str(uuid.uuid4())[:8],
        "text": suggestion_text,
        "user_id": user_id,
        "user_name": user_name,
        "channel": channel,
        "ts": ts,
        "timestamp": datetime.now().isoformat(),
        "status": "pending"
    }

    try:
        # Load existing suggestions
        if os.path.exists(SUGGESTIONS_FILE):
            with open(SUGGESTIONS_FILE, 'r') as f:
                suggestions = json.load(f)
        else:
            suggestions = []

        suggestions.append(suggestion)

        # Save updated suggestions
        with open(SUGGESTIONS_FILE, 'w') as f:
            json.dump(suggestions, f, indent=2)

        logger.info(f"Suggestion saved: {suggestion['id']} from {user_name}")
        return suggestion
    except Exception as e:
        logger.error(f"Failed to save suggestion: {e}")
        return None


def get_pending_suggestions() -> list:
    """Get all pending suggestions."""
    try:
        if os.path.exists(SUGGESTIONS_FILE):
            with open(SUGGESTIONS_FILE, 'r') as f:
                suggestions = json.load(f)
            return [s for s in suggestions if s.get("status") == "pending"]
        return []
    except Exception as e:
        logger.error(f"Failed to load suggestions: {e}")
        return []


def update_suggestion_status(suggestion_id: str, status: str, admin_user: str = None) -> bool:
    """Update the status of a suggestion (approved/rejected)."""
    try:
        if not os.path.exists(SUGGESTIONS_FILE):
            return False

        with open(SUGGESTIONS_FILE, 'r') as f:
            suggestions = json.load(f)

        for suggestion in suggestions:
            if suggestion.get("id") == suggestion_id:
                suggestion["status"] = status
                suggestion["reviewed_by"] = admin_user
                suggestion["reviewed_at"] = datetime.now().isoformat()

                with open(SUGGESTIONS_FILE, 'w') as f:
                    json.dump(suggestions, f, indent=2)

                logger.info(f"Suggestion {suggestion_id} marked as {status} by {admin_user}")
                return True

        return False
    except Exception as e:
        logger.error(f"Failed to update suggestion: {e}")
        return False


def enrich_suggestion(suggestion_text: str) -> str:
    """Use Claude to enrich a suggestion with searchable terms."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""You are helping optimize a knowledge base entry for semantic search in Shaman, a pharma content authoring platform.

ORIGINAL SUGGESTION:
{suggestion_text}

TASK:
Rewrite this as a searchable knowledge entry. Include:
1. The original information (keep it accurate)
2. Expand acronyms (AE=Approved Email, ME=Marketing Email, CLM=Closed Loop Marketing, etc.)
3. Add synonyms and related terms users might search for
4. Mention which builders/features this applies to
5. Add common question phrasings

FORMAT:
Write 3-5 sentences of natural, flowing text. Do NOT use bullet points or headers.
Keep the factual content accurate - just make it more searchable."""
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Failed to enrich suggestion: {e}")
        return suggestion_text  # Fall back to original


def backup_community_collection():
    """Backup community collection to JSON file in background."""
    import threading

    def _do_backup():
        try:
            rag_system = get_rag()
            collection = rag_system.collections["community"]

            # Get all items from collection
            results = collection.get(include=["documents", "metadatas"])

            if not results["ids"]:
                logger.info("Community collection is empty, skipping backup")
                return

            backup_data = []
            for i, doc_id in enumerate(results["ids"]):
                backup_data.append({
                    "id": doc_id,
                    "document": results["documents"][i],
                    "metadata": results["metadatas"][i]
                })

            # Save to backup file
            backup_path = os.path.join(os.path.dirname(__file__), "backups", "community_backup.json")
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)

            with open(backup_path, 'w') as f:
                json.dump(backup_data, f, indent=2)

            logger.info(f"Community backup saved: {len(backup_data)} items to {backup_path}")
        except Exception as e:
            logger.error(f"Failed to backup community collection: {e}")

    # Run backup in background thread
    thread = threading.Thread(target=_do_backup, daemon=True)
    thread.start()


def parse_learn_command(text: str) -> dict:
    """Parse learn command to extract space, question, and answer.

    Supported format:
    learn:
    space: general (or customer name like Takeda)
    question: How do I do X?
    answer: You do Y.
    """
    result = {"space": None, "question": None, "answer": None}

    # Remove "learn:" prefix
    content = text.strip()
    if content.lower().startswith("learn:"):
        content = content[6:].strip()

    # Try to find space:, question:, and answer: markers
    content_lower = content.lower()

    s_idx = content_lower.find("space:")
    q_idx = content_lower.find("question:")
    a_idx = content_lower.find("answer:")

    # Extract space (between "space:" and "question:")
    if s_idx != -1 and q_idx != -1 and s_idx < q_idx:
        space_start = s_idx + len("space:")
        space = content[space_start:q_idx].strip()
        result["space"] = space if space else None

    # Extract question and answer
    if q_idx != -1 and a_idx != -1:
        if q_idx < a_idx:
            question_start = q_idx + len("question:")
            question = content[question_start:a_idx].strip()
            answer = content[a_idx + len("answer:"):].strip()
        else:
            answer_start = a_idx + len("answer:")
            answer = content[answer_start:q_idx].strip()
            question = content[q_idx + len("question:"):].strip()

        result["question"] = question if question else None
        result["answer"] = answer if answer else None

    return result


def validate_learn_space(space: str) -> tuple:
    """Validate the space field for learn command.

    Returns:
        (is_valid, normalized_space, error_message)
        - is_valid: True if valid
        - normalized_space: "general" or customer_key
        - error_message: Error message if invalid
    """
    if not space:
        valid_customers = ", ".join(CUSTOMERS.keys()) if CUSTOMERS else "none configured"
        return (False, None, f"Missing `space:` field. Use `general` or a customer name ({valid_customers})")

    space_lower = space.lower().strip()

    # Check for "general"
    if space_lower == "general":
        return (True, "general", None)

    # Check against customer keys (exact match)
    if space_lower in CUSTOMERS:
        return (True, space_lower, None)

    # Check against customer names (case-insensitive)
    for key, config in CUSTOMERS.items():
        if config.get("name", "").lower() == space_lower:
            return (True, key, None)

    # Not found
    valid_customers = ", ".join(CUSTOMERS.keys()) if CUSTOMERS else "none configured"
    return (False, None, f"Unknown space `{space}`. Use `general` or a customer name ({valid_customers})")


def enrich_qa_pair(question: str, answer: str) -> str:
    """Use Claude to enrich a Q&A pair for better searchability."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""You are helping optimize a Q&A entry for semantic search in Shaman, a pharma content authoring platform.

QUESTION: {question}
ANSWER: {answer}

TASK:
Create a searchable knowledge entry from this Q&A. Include:
1. The question rephrased in multiple ways users might ask it
2. The answer with expanded acronyms (AE=Approved Email, ME=Marketing Email, CLM=Closed Loop Marketing, etc.)
3. Related terms and synonyms users might search for
4. Mention which builders/features this applies to if relevant

FORMAT:
Write as natural, flowing text (3-5 sentences). Start with "Q:" variations, then "A:" with the enriched answer.
Keep the factual content accurate - just make it more searchable."""
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Failed to enrich Q&A: {e}")
        # Fall back to simple format
        return f"Q: {question}\nA: {answer}"


def index_learned_qa(question: str, answer: str, user_id: str, user_name: str, space: str = "general") -> dict:
    """Index a user-submitted Q&A to the appropriate collection.

    Args:
        question: The question text
        answer: The answer text
        user_id: Slack user ID
        user_name: User display name
        space: "general" for community collection, or customer_key for customer collection
    """
    try:
        import uuid
        from datetime import datetime

        rag_system = get_rag()

        # Generate unique ID
        qa_id = str(uuid.uuid4())[:8]
        doc_id = f"learned_{qa_id}"

        # Enrich for better searchability
        enriched_content = enrich_qa_pair(question, answer)
        logger.info(f"Enriched Q&A {qa_id}: {enriched_content[:100]}...")

        # Determine target collection
        if space == "general":
            collection = rag_system.collections["community"]
            collection_name = "community"
        else:
            # Customer-specific collection
            collection = rag_system.get_customer_collection(space)
            collection_name = f"customer_{space}"

        # Add to collection
        collection.add(
            documents=[enriched_content],
            metadatas=[{
                "source": collection_name,
                "type": "learned_qa",
                "space": space,
                "original_question": question[:500],
                "original_answer": answer[:500],
                "submitted_by": user_name,
                "submitted_at": datetime.now().isoformat(),
            }],
            ids=[doc_id]
        )

        logger.info(f"Indexed learned Q&A {qa_id} to {collection_name} collection")

        # Backup community collection if general
        if space == "general":
            backup_community_collection()

        return {
            "id": qa_id,
            "question": question,
            "answer": answer,
            "space": space,
            "enriched": enriched_content
        }
    except Exception as e:
        logger.error(f"Failed to index learned Q&A: {e}")
        return None


def index_approved_suggestion(suggestion: dict) -> bool:
    """Index an approved suggestion into the community collection."""
    try:
        rag_system = get_rag()

        doc_id = f"community_{suggestion['id']}"
        original_text = suggestion["text"]

        # Enrich with AI for better searchability
        enriched_content = enrich_suggestion(original_text)
        logger.info(f"Enriched suggestion {suggestion['id']}: {enriched_content[:100]}...")

        # Add to community collection
        rag_system.collections["community"].add(
            documents=[enriched_content],
            metadatas=[{
                "source": "community",
                "type": "user_contribution",
                "original_text": original_text[:500],  # Keep original for reference
                "submitted_by": suggestion.get("user_name", "unknown"),
                "submitted_at": suggestion.get("timestamp", ""),
                "approved_by": suggestion.get("reviewed_by", ""),
                "approved_at": suggestion.get("reviewed_at", "")
            }],
            ids=[doc_id]
        )

        logger.info(f"Indexed suggestion {suggestion['id']} to community collection")

        # Backup community collection in background
        backup_community_collection()

        return True
    except Exception as e:
        logger.error(f"Failed to index suggestion: {e}")
        return False


def convert_to_slack_markdown(text: str) -> str:
    """Convert standard markdown to Slack mrkdwn format."""
    # Convert **bold** to *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)

    # Convert __bold__ to *bold*
    text = re.sub(r'__(.+?)__', r'*\1*', text)

    # Convert _italic_ to _italic_ (same in Slack)
    # No change needed

    # Convert - bullets to • bullets (at start of line)
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\n- ', '\n• ', text)

    # Convert numbered lists 1. 2. 3. to cleaner format
    # Keep as is - Slack handles these okay

    # Convert [text](url) links to <url|text>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)

    # Convert ### headers to *bold* (Slack doesn't support headers)
    text = re.sub(r'^###\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    return text


def format_slack_response(answer: str, sources: list = None, intent_info: dict = None, include_feedback: bool = True) -> list:
    """Format the answer for Slack with blocks."""
    blocks = []

    # Add indicator for ambiguous questions or specific intents
    if intent_info:
        # Show clarification needed indicator
        if intent_info.get("is_ambiguous"):
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ":thinking_face: *Need more details*"
                    }
                ]
            })
        # Show intent indicator for non-how_to intents
        elif intent_info.get("intent") not in ["how_to", "greeting"]:
            intent = intent_info.get("intent", "unknown")
            intent_emoji = {
                "bug_veeva": ":bug:",
                "bug_config": ":gear:",
                "bug_product": ":bug:",
                "feature_request": ":bulb:",
                "escalation": ":warning:"
            }.get(intent, ":question:")

            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{intent_emoji} *Detected: {intent.replace('_', ' ').title()}*"
                    }
                ]
            })

    # Add the answer (convert markdown to Slack format)
    slack_answer = convert_to_slack_markdown(answer)
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": slack_answer[:3000]  # Slack block text limit
        }
    })

    # Add sources if available (only sources with URLs)
    if sources:
        source_links = []
        for s in sources[:5]:  # Limit to 5 sources
            meta = s.get("metadata", {})
            title = meta.get("title", "")
            url = meta.get("url", "")
            source_type = s.get("source", "")

            # Only include sources that have actual URLs
            if url and title:
                source_links.append(f"<{url}|{title}>")
            elif url:
                source_links.append(f"<{url}|{source_type}>")

        # Only show sources section if we have links
        if source_links:
            # Dedupe links
            source_links = list(dict.fromkeys(source_links))
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Sources:* {' | '.join(source_links[:3])}"
                    }
                ]
            })

    # Add feedback buttons
    if include_feedback and not (intent_info and intent_info.get("is_ambiguous")):
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👍 Helpful", "emoji": True},
                    "style": "primary",
                    "action_id": "feedback_positive"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "👎 Not helpful", "emoji": True},
                    "action_id": "feedback_negative"
                }
            ]
        })

    return blocks


def extract_question(text: str, bot_user_id: str = None) -> str:
    """Extract the question from the message, removing bot mention."""
    # Remove bot mention
    if bot_user_id:
        text = re.sub(f'<@{bot_user_id}>', '', text)

    # Clean up
    text = text.strip()

    return text


@app.event("app_mention")
def handle_mention(event, say, client):
    """Handle @MagicAnswer mentions in channels."""
    try:
        # Get bot user ID to remove from message
        auth_result = client.auth_test()
        bot_user_id = auth_result["user_id"]

        # Extract the question
        question = extract_question(event.get("text", ""), bot_user_id)

        if not question:
            say(
                text="Hi! Ask me a question and I'll search our knowledge base.",
                thread_ts=event.get("thread_ts") or event.get("ts")
            )
            return

        # Handle special commands
        thread_ts = event.get("thread_ts") or event.get("ts")
        if question.lower() in ["!stats", "stats"]:
            rag_system = get_rag()
            stats = []
            for name, collection in rag_system.collections.items():
                count = collection.count()
                if count > 0:
                    stats.append(f"*{name}*: {count}")

            say(
                text=f"*Knowledge Base Stats*\n" + "\n".join(stats),
                thread_ts=thread_ts
            )
            return

        if question.lower() in ["!help", "help"]:
            say(
                text=(
                    "*MagicAnswer Bot*\n\n"
                    "Just ask me any question! I'll search:\n"
                    "- Slack history\n"
                    "- Help Center articles\n"
                    "- Intercom conversations\n"
                    "- Confluence pages\n"
                    "- Veeva documentation\n"
                    "- PDF documents\n\n"
                    "*Commands:*\n"
                    "`stats` - Show knowledge base statistics\n"
                    "`help` - Show this help message"
                ),
                thread_ts=thread_ts
            )
            return

        # Handle suggest command (hidden feature)
        if question.lower().startswith("suggest:"):
            suggestion_text = question[8:].strip()
            if not suggestion_text:
                say(
                    text="Please provide content after `suggest:`. Example:\n`@MagicAnswer suggest: SetToStageCLM sets the CLM status to Staged in Veeva after export`",
                    thread_ts=thread_ts
                )
                return

            # Get user info
            try:
                user_info = client.users_info(user=event["user"])
                user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
            except:
                user_name = event["user"]

            # Save suggestion
            suggestion = save_suggestion(
                suggestion_text=suggestion_text,
                user_id=event["user"],
                user_name=user_name,
                channel=event["channel"],
                ts=event["ts"]
            )

            if suggestion:
                # Log the suggestion
                try:
                    channel_info = client.conversations_info(channel=event["channel"])
                    channel_name = channel_info["channel"].get("name", event["channel"])
                except:
                    channel_name = event["channel"]

                log_question(
                    question=suggestion_text,
                    user_id=event["user"],
                    user_name=user_name,
                    channel=event["channel"],
                    channel_name=channel_name,
                    is_follow_up=False,
                    is_feedback=False,
                    is_suggestion=True,
                    intent_info=None
                )

                # Notify user
                say(
                    text=f"✅ *Suggestion received!* (ID: `{suggestion['id']}`)\n\nYour suggestion has been submitted for admin review. Once approved, it will be added to the knowledge base.\n\n> {suggestion_text[:200]}{'...' if len(suggestion_text) > 200 else ''}",
                    thread_ts=thread_ts
                )

                # Notify admin channel if configured
                if ADMIN_CHANNEL:
                    try:
                        client.chat_postMessage(
                            channel=ADMIN_CHANNEL,
                            text=f"📝 *New Knowledge Suggestion*",
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"📝 *New Knowledge Suggestion* (ID: `{suggestion['id']}`)"
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*From:* <@{event['user']}>\n*Content:*\n> {suggestion_text}"
                                    }
                                },
                                {
                                    "type": "context",
                                    "elements": [
                                        {
                                            "type": "mrkdwn",
                                            "text": f"React with ✅ to approve or ❌ to reject | ID: `{suggestion['id']}`"
                                        }
                                    ]
                                }
                            ]
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify admin channel: {e}")
            else:
                say(
                    text="❌ Sorry, there was an error saving your suggestion. Please try again.",
                    thread_ts=thread_ts
                )
            return

        logger.info(f"Question from mention: {question[:100]}...")

        # Show thinking indicator
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        is_follow_up = bool(event.get("thread_ts"))

        # Get user info for logging
        try:
            user_info = client.users_info(user=event["user"])
            profile = user_info["user"]["profile"]
            user_name = profile.get("display_name") or profile.get("real_name") or profile.get("name") or event["user"]
            # Handle empty strings
            if not user_name.strip():
                user_name = event["user"]
        except Exception as e:
            logger.debug(f"Could not get user info: {e}")
            user_name = event["user"]

        # Get channel name for logging
        try:
            channel_info = client.conversations_info(channel=channel)
            channel_name = channel_info["channel"].get("name") or channel
        except Exception as e:
            logger.debug(f"Could not get channel info: {e}")
            channel_name = channel

        thinking_msg = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Searching knowledge base..."
        )

        # Get thread context for follow-up questions
        thread_context = get_thread_context(client, channel, event.get("thread_ts"))

        # Detect if this is a customer-specific channel
        customer_key = get_customer_by_channel(channel)
        if customer_key:
            customer_config = get_customer_config(customer_key)
            customer_name = customer_config.get("name", customer_key) if customer_config else customer_key
            logger.info(f"Customer detected: {customer_name} (channel: {channel})")

        # Get answer from RAG
        rag_system = get_rag()

        # Search for context (include customer-specific docs if applicable)
        results = rag_system.search(question, n_results=10, customer_key=customer_key)

        # Get answer with intent classification and thread context
        answer, intent_info = rag_system.ask(question, thread_context=thread_context, customer_key=customer_key)

        # Log the question
        log_question(
            question=question,
            user_id=event["user"],
            user_name=user_name,
            channel=channel,
            channel_name=channel_name,
            is_follow_up=is_follow_up,
            is_feedback=False,
            is_suggestion=False,
            intent_info=intent_info,
            customer_key=customer_key
        )

        # Format response with intent indicator and feedback buttons
        blocks = format_slack_response(answer, results, intent_info)

        # Update the thinking message with the answer
        client.chat_update(
            channel=channel,
            ts=thinking_msg["ts"],
            text=answer[:3000],
            blocks=blocks
        )

    except Exception as e:
        logger.error(f"Error handling mention: {e}")
        say(
            text=f"Sorry, I encountered an error: {str(e)[:200]}",
            thread_ts=event.get("thread_ts") or event.get("ts")
        )


@app.event("message")
def handle_message(event, say, client):
    """Handle direct messages and thread replies."""
    # Ignore messages from bots (including ourselves)
    if event.get("bot_id") or event.get("subtype"):
        return

    channel_type = event.get("channel_type", "")
    thread_ts = event.get("thread_ts")
    channel = event.get("channel")

    # Check if this is a thread reply in a channel (not DM)
    if channel_type != "im" and thread_ts:
        # Check if bot has participated in this thread
        try:
            auth_result = client.auth_test()
            bot_user_id = auth_result["user_id"]

            # Get thread replies to check if bot is in this thread
            replies = client.conversations_replies(
                channel=channel,
                ts=thread_ts,
                limit=50
            )

            bot_in_thread = False
            for msg in replies.get("messages", []):
                if msg.get("user") == bot_user_id or msg.get("bot_id"):
                    # Check if it's our bot (has our typical response pattern)
                    if "Knowledge Base" in msg.get("text", "") or msg.get("user") == bot_user_id:
                        bot_in_thread = True
                        break

            if not bot_in_thread:
                return  # Bot hasn't participated, ignore this thread

            # Bot is in thread - handle as follow-up question
            question = event.get("text", "").strip()

            # Remove any bot mentions
            question = re.sub(f'<@{bot_user_id}>', '', question).strip()

            if not question:
                return

            # Handle suggest command in thread
            if question.lower().startswith("suggest:"):
                suggestion_text = question[8:].strip()
                if not suggestion_text:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="Please provide content after `suggest:`. Example:\n`suggest: SetToStageCLM sets the CLM status to Staged in Veeva after export`"
                    )
                    return

                # Get user info
                try:
                    user_info = client.users_info(user=event["user"])
                    user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
                except:
                    user_name = event["user"]

                # Save suggestion
                suggestion = save_suggestion(
                    suggestion_text=suggestion_text,
                    user_id=event["user"],
                    user_name=user_name,
                    channel=channel,
                    ts=event["ts"]
                )

                if suggestion:
                    # Notify user
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"✅ *Suggestion received!* (ID: `{suggestion['id']}`)\n\nYour suggestion has been submitted for admin review. Once approved, it will be added to the knowledge base.\n\n> {suggestion_text[:200]}{'...' if len(suggestion_text) > 200 else ''}"
                    )

                    # Notify admin channel if configured
                    if ADMIN_CHANNEL:
                        try:
                            client.chat_postMessage(
                                channel=ADMIN_CHANNEL,
                                text=f"📝 *New Knowledge Suggestion*",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"📝 *New Knowledge Suggestion* (ID: `{suggestion['id']}`)"
                                        }
                                    },
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"*From:* <@{event['user']}>\n*Content:*\n> {suggestion_text}"
                                        }
                                    },
                                    {
                                        "type": "context",
                                        "elements": [
                                            {
                                                "type": "mrkdwn",
                                                "text": f"React with ✅ to approve or ❌ to reject | ID: `{suggestion['id']}`"
                                            }
                                        ]
                                    }
                                ]
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify admin channel: {e}")
                else:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="❌ Sorry, there was an error saving your suggestion. Please try again."
                    )
                return

            logger.info(f"Follow-up question in thread: {question[:100]}...")

            # Get user info for logging
            try:
                user_info = client.users_info(user=event["user"])
                profile = user_info["user"]["profile"]
                user_name = profile.get("display_name") or profile.get("real_name") or profile.get("name") or event["user"]
                if not user_name.strip():
                    user_name = event["user"]
            except Exception as e:
                logger.debug(f"Could not get user info: {e}")
                user_name = event["user"]

            # Get channel name for logging
            try:
                channel_info = client.conversations_info(channel=channel)
                channel_name = channel_info["channel"].get("name") or channel
            except Exception as e:
                logger.debug(f"Could not get channel info: {e}")
                channel_name = channel

            # Show thinking indicator
            thinking_msg = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Searching knowledge base..."
            )

            # Get thread context for follow-up
            thread_context = get_thread_context(client, channel, thread_ts)

            # Detect if this is a customer-specific channel
            customer_key = get_customer_by_channel(channel)
            if customer_key:
                customer_config = get_customer_config(customer_key)
                customer_name = customer_config.get("name", customer_key) if customer_config else customer_key
                logger.info(f"Customer detected: {customer_name} (channel: {channel})")

            # Get answer from RAG
            rag_system = get_rag()
            results = rag_system.search(question, n_results=10, customer_key=customer_key)
            answer, intent_info = rag_system.ask(question, thread_context=thread_context, customer_key=customer_key)

            # Log the question
            log_question(
                question=question,
                user_id=event["user"],
                user_name=user_name,
                channel=channel,
                channel_name=channel_name,
                is_follow_up=True,
                is_feedback=False,
                is_suggestion=False,
                intent_info=intent_info,
                customer_key=customer_key
            )

            # Format response
            blocks = format_slack_response(answer, results, intent_info)

            # Update the thinking message
            client.chat_update(
                channel=channel,
                ts=thinking_msg["ts"],
                text=convert_to_slack_markdown(answer)[:3000],
                blocks=blocks
            )
            return

        except Exception as e:
            logger.error(f"Error handling thread reply: {e}")
            return

    # Only handle DMs (im = instant message)
    if channel_type != "im":
        return

    try:
        question = event.get("text", "").strip()

        # Handle DM thread replies (thread_ts exists means it's a reply in a thread)
        if thread_ts:
            logger.info(f"DM thread reply detected: {question[:50]}...")

            # Handle suggest command in DM thread
            if question.lower().startswith("suggest:"):
                suggestion_text = question[8:].strip()
                if not suggestion_text:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="Please provide content after `suggest:`. Example:\n`suggest: SetToStageCLM sets the CLM status to Staged in Veeva after export`"
                    )
                    return

                # Get user info
                try:
                    user_info = client.users_info(user=event["user"])
                    user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
                except:
                    user_name = event["user"]

                # Save suggestion
                suggestion = save_suggestion(
                    suggestion_text=suggestion_text,
                    user_id=event["user"],
                    user_name=user_name,
                    channel=channel,
                    ts=event["ts"]
                )

                if suggestion:
                    # Log the suggestion
                    log_question(
                        question=suggestion_text,
                        user_id=event["user"],
                        user_name=user_name,
                        channel=channel,
                        channel_name="DM Thread",
                        is_follow_up=True,
                        is_feedback=False,
                        is_suggestion=True,
                        intent_info=None
                    )

                    # Notify user in thread
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"✅ *Suggestion received!* (ID: `{suggestion['id']}`)\n\nYour suggestion has been submitted for admin review. Once approved, it will be added to the knowledge base.\n\n> {suggestion_text[:200]}{'...' if len(suggestion_text) > 200 else ''}"
                    )

                    # Notify admin channel if configured
                    if ADMIN_CHANNEL:
                        try:
                            client.chat_postMessage(
                                channel=ADMIN_CHANNEL,
                                text=f"📝 *New Knowledge Suggestion*",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"📝 *New Knowledge Suggestion* (ID: `{suggestion['id']}`)"
                                        }
                                    },
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"*From:* <@{event['user']}> (DM Thread)\n*Content:*\n> {suggestion_text}"
                                        }
                                    },
                                    {
                                        "type": "context",
                                        "elements": [
                                            {
                                                "type": "mrkdwn",
                                                "text": f"React with ✅ to approve or ❌ to reject | ID: `{suggestion['id']}`"
                                            }
                                        ]
                                    }
                                ]
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify admin channel: {e}")
                else:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="❌ Sorry, there was an error saving your suggestion. Please try again."
                    )
                return

            # Handle learn command in DM thread
            if question.lower().startswith("learn:"):
                parsed = parse_learn_command(question)

                # Build validation error messages
                errors = []
                if not parsed["space"]:
                    errors.append("• Missing `space:` field")
                if not parsed["question"]:
                    errors.append("• Missing `question:` field")
                if not parsed["answer"]:
                    errors.append("• Missing `answer:` field")

                if errors:
                    valid_customers = ", ".join(CUSTOMERS.keys()) if CUSTOMERS else "none configured"
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"❌ *Missing required fields:*\n" + "\n".join(errors) + f"\n\n*Format:*\n```learn:\nspace: general (or {valid_customers})\nquestion: How do I configure CLM sync?\nanswer: Go to Admin > Sync Settings and enable CLM sync.```"
                    )
                    return

                # Validate space
                is_valid, normalized_space, space_error = validate_learn_space(parsed["space"])
                if not is_valid:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"❌ {space_error}"
                    )
                    return

                # Get user info
                try:
                    user_info = client.users_info(user=event["user"])
                    user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
                except:
                    user_name = event["user"]

                # Show processing message
                space_display = "general knowledge" if normalized_space == "general" else f"{CUSTOMERS.get(normalized_space, {}).get('name', normalized_space)} knowledge"
                thinking_msg = client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"📚 Learning this Q&A to {space_display}..."
                )

                # Index the Q&A directly
                result = index_learned_qa(
                    question=parsed["question"],
                    answer=parsed["answer"],
                    user_id=event["user"],
                    user_name=user_name,
                    space=normalized_space
                )

                if result:
                    client.chat_update(
                        channel=channel,
                        ts=thinking_msg["ts"],
                        text=f"✅ *Learned!* (ID: `{result['id']}`)\n\n*Space:* {space_display}\n*Q:* {parsed['question'][:200]}\n*A:* {parsed['answer'][:200]}\n\nThis Q&A is now searchable in the knowledge base."
                    )

                    logger.info(f"User {user_name} taught Q&A {result['id']} to {normalized_space}")

                    # Notify admin channel
                    if ADMIN_CHANNEL:
                        try:
                            client.chat_postMessage(
                                channel=ADMIN_CHANNEL,
                                text=f"📚 *New Learned Q&A* (ID: `{result['id']}`)",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"📚 *New Learned Q&A* (ID: `{result['id']}`)"
                                        }
                                    },
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"*From:* <@{event['user']}>\n*Space:* {space_display}\n\n*Q:* {parsed['question'][:300]}\n*A:* {parsed['answer'][:300]}"
                                        }
                                    },
                                    {
                                        "type": "context",
                                        "elements": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "ℹ️ This Q&A was indexed immediately (no approval needed)"
                                            }
                                        ]
                                    }
                                ]
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify admin channel: {e}")
                else:
                    client.chat_update(
                        channel=channel,
                        ts=thinking_msg["ts"],
                        text="❌ Sorry, there was an error learning this Q&A. Please try again."
                    )
                return

            # Handle other DM thread replies as follow-up questions
            # (continue to regular handling below, but could add thread context here)

        if not question:
            say("Hi! Ask me a question and I'll search our knowledge base.")
            return

        # Handle special commands (use ! prefix to avoid Slack slash command conflicts)
        if question.lower() in ["!stats", "stats"]:
            rag_system = get_rag()
            stats = []
            for name, collection in rag_system.collections.items():
                count = collection.count()
                if count > 0:
                    stats.append(f"*{name}*: {count}")

            say(f"*Knowledge Base Stats*\n" + "\n".join(stats))
            return

        if question.lower() in ["!help", "help"]:
            say(
                "*MagicAnswer Bot*\n\n"
                "Just ask me any question! I'll search:\n"
                "- Slack history\n"
                "- Help Center articles\n"
                "- Intercom conversations\n"
                "- Confluence pages\n"
                "- Veeva documentation\n"
                "- PDF documents\n\n"
                "*Commands:*\n"
                "`stats` - Show knowledge base statistics\n"
                "`help` - Show this help message\n"
                "`suggest: <info>` - Submit knowledge for review\n"
                "`learn:` - Teach a Q&A (requires space, question, answer)"
            )
            return

        # Handle suggest command in DMs
        if question.lower().startswith("suggest:"):
            suggestion_text = question[8:].strip()
            if not suggestion_text:
                say("Please provide content after `suggest:`. Example:\n`suggest: SetToStageCLM sets the CLM status to Staged in Veeva after export`")
                return

            # Get user info
            try:
                user_info = client.users_info(user=event["user"])
                user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
            except:
                user_name = event["user"]

            # Save suggestion
            suggestion = save_suggestion(
                suggestion_text=suggestion_text,
                user_id=event["user"],
                user_name=user_name,
                channel=event["channel"],
                ts=event["ts"]
            )

            if suggestion:
                # Log the suggestion
                log_question(
                    question=suggestion_text,
                    user_id=event["user"],
                    user_name=user_name,
                    channel=event["channel"],
                    channel_name="DM",
                    is_follow_up=False,
                    is_feedback=False,
                    is_suggestion=True,
                    intent_info=None
                )

                # Notify user
                say(f"✅ *Suggestion received!* (ID: `{suggestion['id']}`)\n\nYour suggestion has been submitted for admin review. Once approved, it will be added to the knowledge base.\n\n> {suggestion_text[:200]}{'...' if len(suggestion_text) > 200 else ''}")

                # Notify admin channel if configured
                if ADMIN_CHANNEL:
                    try:
                        client.chat_postMessage(
                            channel=ADMIN_CHANNEL,
                            text=f"📝 *New Knowledge Suggestion*",
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"📝 *New Knowledge Suggestion* (ID: `{suggestion['id']}`)"
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*From:* <@{event['user']}> (DM)\n*Content:*\n> {suggestion_text}"
                                    }
                                },
                                {
                                    "type": "context",
                                    "elements": [
                                        {
                                            "type": "mrkdwn",
                                            "text": f"React with ✅ to approve or ❌ to reject | ID: `{suggestion['id']}`"
                                        }
                                    ]
                                }
                            ]
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify admin channel: {e}")
            else:
                say("❌ Sorry, there was an error saving your suggestion. Please try again.")
            return

        # Handle learn command in DMs
        if question.lower().startswith("learn:"):
            parsed = parse_learn_command(question)

            # Build validation error messages
            errors = []
            if not parsed["space"]:
                errors.append("• Missing `space:` field")
            if not parsed["question"]:
                errors.append("• Missing `question:` field")
            if not parsed["answer"]:
                errors.append("• Missing `answer:` field")

            if errors:
                valid_customers = ", ".join(CUSTOMERS.keys()) if CUSTOMERS else "none configured"
                say(f"❌ *Missing required fields:*\n" + "\n".join(errors) + f"\n\n*Format:*\n```learn:\nspace: general (or {valid_customers})\nquestion: How do I configure CLM sync?\nanswer: Go to Admin > Sync Settings and enable CLM sync.```")
                return

            # Validate space
            is_valid, normalized_space, space_error = validate_learn_space(parsed["space"])
            if not is_valid:
                say(f"❌ {space_error}")
                return

            # Get user info
            try:
                user_info = client.users_info(user=event["user"])
                user_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or event["user"]
            except:
                user_name = event["user"]

            # Show processing message
            space_display = "general knowledge" if normalized_space == "general" else f"{CUSTOMERS.get(normalized_space, {}).get('name', normalized_space)} knowledge"
            thinking_msg = say(f"📚 Learning this Q&A to {space_display}...")

            # Index the Q&A directly
            result = index_learned_qa(
                question=parsed["question"],
                answer=parsed["answer"],
                user_id=event["user"],
                user_name=user_name,
                space=normalized_space
            )

            if result:
                client.chat_update(
                    channel=event["channel"],
                    ts=thinking_msg["ts"],
                    text=f"✅ *Learned!* (ID: `{result['id']}`)\n\n*Space:* {space_display}\n*Q:* {parsed['question'][:200]}\n*A:* {parsed['answer'][:200]}\n\nThis Q&A is now searchable in the knowledge base."
                )

                logger.info(f"User {user_name} taught Q&A {result['id']} to {normalized_space}")

                # Notify admin channel
                if ADMIN_CHANNEL:
                    try:
                        client.chat_postMessage(
                            channel=ADMIN_CHANNEL,
                            text=f"📚 *New Learned Q&A* (ID: `{result['id']}`)",
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"📚 *New Learned Q&A* (ID: `{result['id']}`)"
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*From:* <@{event['user']}>\n*Space:* {space_display}\n\n*Q:* {parsed['question'][:300]}\n*A:* {parsed['answer'][:300]}"
                                    }
                                },
                                {
                                    "type": "context",
                                    "elements": [
                                        {
                                            "type": "mrkdwn",
                                            "text": "ℹ️ This Q&A was indexed immediately (no approval needed)"
                                        }
                                    ]
                                }
                            ]
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify admin channel: {e}")
            else:
                client.chat_update(
                    channel=event["channel"],
                    ts=thinking_msg["ts"],
                    text="❌ Sorry, there was an error learning this Q&A. Please try again."
                )
            return

        logger.info(f"Question from DM: {question[:100]}...")

        # Get user info for logging
        try:
            user_info = client.users_info(user=event["user"])
            profile = user_info["user"]["profile"]
            user_name = profile.get("display_name") or profile.get("real_name") or profile.get("name") or event["user"]
            if not user_name.strip():
                user_name = event["user"]
        except Exception as e:
            logger.debug(f"Could not get user info: {e}")
            user_name = event["user"]

        # Show thinking indicator
        thinking_msg = say("Searching knowledge base...")

        # Get answer from RAG
        rag_system = get_rag()

        # Search for context
        results = rag_system.search(question, n_results=10)

        # Get answer with intent classification
        answer, intent_info = rag_system.ask(question)

        # Log the question
        log_question(
            question=question,
            user_id=event["user"],
            user_name=user_name,
            channel=event["channel"],
            channel_name="DM",
            is_follow_up=False,
            is_feedback=False,
            is_suggestion=False,
            intent_info=intent_info
        )

        # Format response with intent indicator
        blocks = format_slack_response(answer, results, intent_info)

        # Update the thinking message with the answer
        client.chat_update(
            channel=event["channel"],
            ts=thinking_msg["ts"],
            text=answer[:3000],
            blocks=blocks
        )

    except Exception as e:
        logger.error(f"Error handling DM: {e}")
        say(f"Sorry, I encountered an error: {str(e)[:200]}")


@app.event("app_home_opened")
def handle_app_home(client, event):
    """Update the App Home tab when opened."""
    try:
        rag_system = get_rag()

        # Build stats
        stats_blocks = []
        total = 0

        source_emojis = {
            "slack": ":speech_balloon:",
            "helpcenter": ":books:",
            "intercom": ":ticket:",
            "veeva": ":green_book:",
            "pdf": ":page_facing_up:",
            "manual": ":memo:",
            "confluence": ":blue_book:"
        }

        for name, collection in rag_system.collections.items():
            count = collection.count()
            total += count
            emoji = source_emojis.get(name, ":file_folder:")
            stats_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{name.title()}*: {count:,} items"
                }
            })

        # Add customer stats
        customer_stats = rag_system.get_customer_stats()
        if any(v > 0 for v in customer_stats.values()):
            stats_blocks.append({"type": "divider"})
            stats_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":office: *Customer Collections*"
                }
            })
            for cust_key, count in customer_stats.items():
                if count > 0:
                    total += count
                    stats_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"   • *{cust_key.title()}*: {count:,} items"
                        }
                    })

        client.views_publish(
            user_id=event["user"],
            view={
                "type": "home",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "MagicAnswer"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Ask me questions in DMs or mention me in channels!"
                        }
                    },
                    {"type": "divider"},
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"Knowledge Base ({total:,} total items)"
                        }
                    },
                    *stats_blocks
                ]
            }
        )
    except Exception as e:
        logger.error(f"Error updating app home: {e}")
        # Publish fallback view so it doesn't hang
        try:
            client.views_publish(
                user_id=event["user"],
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "MagicAnswer"}
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Ask me questions in DMs or mention me in channels!\n\n_Knowledge base stats temporarily unavailable._"
                            }
                        }
                    ]
                }
            )
        except:
            pass


@app.action("feedback_positive")
def handle_positive_feedback(ack, body, client):
    """Handle positive feedback button click."""
    ack()
    try:
        user_id = body["user"]["id"]
        message = body.get("message", {})

        # Extract question and answer from the message
        # The question is in the original message that triggered this
        blocks = message.get("blocks", [])
        answer = ""
        for block in blocks:
            if block.get("type") == "section":
                answer = block.get("text", {}).get("text", "")
                break

        # Save feedback
        save_feedback(
            question="[from feedback button]",
            answer=answer,
            feedback="positive",
            user_id=user_id
        )

        # Update message to show feedback received
        new_blocks = [b for b in blocks if b.get("type") != "actions"]
        new_blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "✅ _Thanks for the feedback!_"}]
        })

        client.chat_update(
            channel=body["channel"]["id"],
            ts=message["ts"],
            blocks=new_blocks,
            text=message.get("text", "")
        )
    except Exception as e:
        logger.error(f"Error handling positive feedback: {e}")


@app.action("feedback_negative")
def handle_negative_feedback(ack, body, client):
    """Handle negative feedback button click."""
    ack()
    try:
        user_id = body["user"]["id"]
        message = body.get("message", {})

        # Extract answer from the message
        blocks = message.get("blocks", [])
        answer = ""
        for block in blocks:
            if block.get("type") == "section":
                answer = block.get("text", {}).get("text", "")
                break

        # Save feedback
        save_feedback(
            question="[from feedback button]",
            answer=answer,
            feedback="negative",
            user_id=user_id
        )

        # Update message to show feedback received
        new_blocks = [b for b in blocks if b.get("type") != "actions"]
        new_blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "📝 _Thanks for the feedback! We'll work on improving._"}]
        })

        client.chat_update(
            channel=body["channel"]["id"],
            ts=message["ts"],
            blocks=new_blocks,
            text=message.get("text", "")
        )
    except Exception as e:
        logger.error(f"Error handling negative feedback: {e}")


@app.event("reaction_added")
def handle_reaction(event, client):
    """Handle reaction events for suggestion approval/rejection."""
    try:
        reaction = event.get("reaction", "")
        user_id = event.get("user", "")
        item = event.get("item", {})

        # Only process reactions in admin channel
        if item.get("channel") != ADMIN_CHANNEL:
            return

        # Only process approval/rejection reactions
        if reaction not in ["white_check_mark", "x", "heavy_check_mark", "cross_mark"]:
            return

        # Get the message that was reacted to
        try:
            result = client.conversations_history(
                channel=item["channel"],
                latest=item["ts"],
                limit=1,
                inclusive=True
            )
            messages = result.get("messages", [])
            if not messages:
                return

            message = messages[0]
            message_text = message.get("text", "")
            blocks = message.get("blocks", [])

            # Extract suggestion ID from the message
            suggestion_id = None
            for block in blocks:
                if block.get("type") == "context":
                    for element in block.get("elements", []):
                        text = element.get("text", "")
                        if "ID: `" in text:
                            # Extract ID between backticks
                            import re
                            match = re.search(r'ID: `([^`]+)`', text)
                            if match:
                                suggestion_id = match.group(1)
                                break

            if not suggestion_id:
                return

            # Get admin user info
            try:
                user_info = client.users_info(user=user_id)
                admin_name = user_info["user"]["profile"].get("display_name") or user_info["user"]["profile"].get("real_name") or user_id
            except:
                admin_name = user_id

            # Process approval or rejection
            is_approval = reaction in ["white_check_mark", "heavy_check_mark"]

            if is_approval:
                # Load the suggestion to index it
                if os.path.exists(SUGGESTIONS_FILE):
                    with open(SUGGESTIONS_FILE, 'r') as f:
                        suggestions = json.load(f)

                    suggestion = next((s for s in suggestions if s.get("id") == suggestion_id), None)

                    if suggestion and suggestion.get("status") == "pending":
                        # Update status
                        update_suggestion_status(suggestion_id, "approved", admin_name)

                        # Update suggestion object with review info for indexing
                        suggestion["reviewed_by"] = admin_name
                        suggestion["reviewed_at"] = datetime.now().isoformat()

                        # Index the suggestion
                        if index_approved_suggestion(suggestion):
                            # Update the admin message
                            client.chat_postMessage(
                                channel=item["channel"],
                                thread_ts=item["ts"],
                                text=f"✅ *Approved* by <@{user_id}> and indexed to knowledge base."
                            )

                            # Notify the original suggester
                            try:
                                client.chat_postMessage(
                                    channel=suggestion["user_id"],
                                    text=f"🎉 Your suggestion (ID: `{suggestion_id}`) has been approved and added to the MagicAnswer knowledge base!\n\n> {suggestion['text'][:200]}..."
                                )
                            except:
                                pass
                        else:
                            client.chat_postMessage(
                                channel=item["channel"],
                                thread_ts=item["ts"],
                                text=f"⚠️ Approved but failed to index. Please check logs."
                            )
            else:
                # Rejection
                update_suggestion_status(suggestion_id, "rejected", admin_name)

                # Update the admin message
                client.chat_postMessage(
                    channel=item["channel"],
                    thread_ts=item["ts"],
                    text=f"❌ *Rejected* by <@{user_id}>."
                )

        except Exception as e:
            logger.error(f"Error processing reaction for suggestion: {e}")

    except Exception as e:
        logger.error(f"Error handling reaction: {e}")


def graceful_shutdown(signum, frame):
    """Handle shutdown signals gracefully."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, shutting down gracefully...")
    print(f"\n{sig_name} received. Shutting down gracefully...")
    # Give ChromaDB time to finish any pending writes
    sys.exit(0)


def main():
    """Start the Slack bot."""
    # Set up graceful shutdown handlers
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    # Check for required tokens
    bot_token = os.environ.get("SLACK_MAGICANSWER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")

    if not bot_token:
        print("Error: SLACK_MAGICANSWER_TOKEN not found in .env")
        print("Get it from: https://api.slack.com/apps → MagicAnswer → OAuth & Permissions")
        return

    if not app_token:
        print("Error: SLACK_APP_TOKEN not found in .env")
        print("Get it from: https://api.slack.com/apps → Your App → Basic Information → App-Level Tokens")
        return

    # Pre-initialize RAG to load on startup
    print("Loading knowledge base...")
    get_rag()

    # Show stats
    stats = []
    for name, collection in rag.collections.items():
        count = collection.count()
        if count > 0:
            stats.append(f"  {name}: {count}")

    print("\nKnowledge base loaded:")
    print("\n".join(stats))

    print("\n" + "=" * 50)
    print("MagicAnswer Slack Bot is running!")
    print("=" * 50)
    print("\nThe bot will respond to:")
    print("  - @MagicAnswer mentions in channels")
    print("  - Direct messages")
    print("\nPress Ctrl+C to stop")
    print("=" * 50 + "\n")

    # Start the bot
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
