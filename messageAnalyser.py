"""
Slack Channel Analyzer

5. RUN THE SCRIPT
   Update CHANNEL_NAME below and run!

"""

import os
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic

import certifi
import ssl

ssl._create_default_https_context = ssl.create_default_context
ssl._create_default_https_context().load_verify_locations(certifi.where())

# Load environment variables
load_dotenv()

# Configuration
CHANNEL_NAME = "product-questions"  # Change this to your channel name
DAYS_TO_ANALYZE = 30  # How many days back to look
MAX_MESSAGES = 500  # Maximum messages to fetch


def get_slack_client():
    """Initialize Slack client."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN not found in environment. Check your .env file.")
    return WebClient(token=token)


def get_anthropic_client():
    """Initialize Anthropic client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in environment. Check your .env file.")
    return anthropic.Anthropic(api_key=api_key)


def find_channel_id(client: WebClient, channel_name: str) -> str:
    """Find channel ID by name."""
    try:
        # Try public channels first
        result = client.conversations_list(types="public_channel,private_channel")
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]

        # Paginate if needed
        while result.get("response_metadata", {}).get("next_cursor"):
            result = client.conversations_list(
                types="public_channel,private_channel",
                cursor=result["response_metadata"]["next_cursor"]
            )
            for channel in result["channels"]:
                if channel["name"] == channel_name:
                    return channel["id"]

        raise ValueError(f"Channel '{channel_name}' not found. Make sure the bot is invited to the channel.")

    except SlackApiError as e:
        raise ValueError(f"Error finding channel: {e.response['error']}")


def get_user_names(client: WebClient, user_ids: set) -> dict:
    """Fetch display names for user IDs."""
    user_names = {}
    for user_id in user_ids:
        try:
            result = client.users_info(user=user_id)
            profile = result["user"]["profile"]
            user_names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
        except SlackApiError:
            user_names[user_id] = user_id
    return user_names


def fetch_messages(client: WebClient, channel_id: str, days: int, max_messages: int) -> list:
    """Fetch messages from channel."""
    messages = []
    oldest = datetime.now() - timedelta(days=days)
    oldest_ts = oldest.timestamp()

    try:
        result = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest_ts),
            limit=min(max_messages, 200)
        )
        messages.extend(result["messages"])

        # Paginate if needed
        while result.get("has_more") and len(messages) < max_messages:
            result = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest_ts),
                limit=min(max_messages - len(messages), 200),
                cursor=result["response_metadata"]["next_cursor"]
            )
            messages.extend(result["messages"])

        print(f"Fetched {len(messages)} messages from the last {days} days")
        return messages[:max_messages]

    except SlackApiError as e:
        raise ValueError(f"Error fetching messages: {e.response['error']}")


def fetch_thread_replies(client: WebClient, channel_id: str, thread_ts: str) -> list:
    """Fetch replies in a thread."""
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        return result["messages"][1:]  # Exclude parent message
    except SlackApiError:
        return []


def format_messages_for_analysis(messages: list, user_names: dict) -> str:
    """Format messages into readable text for AI analysis."""
    formatted = []

    for msg in messages:
        if msg.get("subtype"):  # Skip system messages
            continue

        user = user_names.get(msg.get("user", ""), "Unknown")
        text = msg.get("text", "")
        ts = datetime.fromtimestamp(float(msg["ts"]))
        date_str = ts.strftime("%Y-%m-%d %H:%M")

        # Replace user mentions with names
        for user_id, name in user_names.items():
            text = text.replace(f"<@{user_id}>", f"@{name}")

        formatted.append(f"[{date_str}] {user}: {text}")

        # Include thread reply count if present
        if msg.get("reply_count", 0) > 0:
            formatted.append(f"  └─ ({msg['reply_count']} replies in thread)")

    return "\n".join(formatted)


def analyze_with_claude(client: anthropic.Anthropic, messages_text: str, channel_name: str) -> str:
    """Send messages to Claude for analysis."""

    prompt = f"""Analyze these Slack messages from the #{channel_name} channel. Focus on:

1. **Questions Asked**: List the main questions people asked the product team. Group similar questions together.

2. **Common Themes**: What topics come up repeatedly? What are people struggling with or asking about most?

3. **Feature Requests**: Any feature requests or improvement suggestions mentioned?

4. **Pain Points**: What frustrations or blockers do people mention?

5. **Actionable Insights**: Based on this data, what should the product team prioritize or address?

Be specific and include examples from the messages where relevant.

---

MESSAGES:
{messages_text}
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def main():
    print("=" * 60)
    print("SLACK CHANNEL ANALYZER")
    print("=" * 60)
    print()

    # Initialize clients
    print("Initializing clients...")
    slack_client = get_slack_client()
    anthropic_client = get_anthropic_client()

    # Find channel
    print(f"Looking for channel: #{CHANNEL_NAME}")
    channel_id = find_channel_id(slack_client, CHANNEL_NAME)
    print(f"Found channel ID: {channel_id}")

    # Fetch messages
    print(f"\nFetching messages from the last {DAYS_TO_ANALYZE} days...")
    messages = fetch_messages(slack_client, channel_id, DAYS_TO_ANALYZE, MAX_MESSAGES)

    if not messages:
        print("No messages found in the specified time range.")
        return

    # Get user names
    print("Fetching user information...")
    user_ids = {msg.get("user") for msg in messages if msg.get("user")}
    user_names = get_user_names(slack_client, user_ids)

    # Format messages
    print("Formatting messages for analysis...")
    messages_text = format_messages_for_analysis(messages, user_names)

    # Analyze with Claude
    print("\nAnalyzing with Claude AI...")
    print("-" * 60)

    analysis = analyze_with_claude(anthropic_client, messages_text, CHANNEL_NAME)

    print("\n" + "=" * 60)
    print("ANALYSIS RESULTS")
    print("=" * 60)
    print()
    print(analysis)

    # Save to file
    output_file = f"slack_analysis_{CHANNEL_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Slack Channel Analysis: #{CHANNEL_NAME}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Messages analyzed: {len(messages)}\n")
        f.write(f"Time range: Last {DAYS_TO_ANALYZE} days\n")
        f.write("=" * 60 + "\n\n")
        f.write(analysis)

    print(f"\n✓ Analysis saved to: {output_file}")


if __name__ == "__main__":
    main()